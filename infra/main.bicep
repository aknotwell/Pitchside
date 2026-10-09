targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('azd environment name, e.g. matchlens-dev')
param environmentName string

@minLength(1)
param location string

@description('Object id of the developer running azd (gets data-plane roles for local dev).')
param principalId string = ''
param principalType string = 'User'

@description('Optional separate region for Cosmos DB if the main region has no capacity.')
param cosmosLocation string = ''

@allowed([ 'Free_F1', 'Standard_S1' ])
param webPubSubSku string = 'Free_F1'

@description('Demo/judging mode: min 1 replica everywhere and always-ready Functions (no cold starts).')
param demoMode bool = false

@description('Email for operational alerts (optional).')
param alertEmail string = ''

param apiExists bool = false
param interpreterExists bool = false
param storytellerExists bool = false

var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName, project: 'matchlens' }

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module identities 'modules/identities.bicep' = {
  name: 'identities'
  scope: rg
  params: { location: location, tags: tags, resourceToken: resourceToken }
}

var ids = identities.outputs.identities
var svc = 'ServicePrincipal'
var hasUser = !empty(principalId)
var user = hasUser ? [ { principalId: principalId, principalType: principalType } ] : []

// Built-in role ids
var roles = {
  ehSender: '2b629674-e913-4c01-ae53-ef4638d8f975'
  ehReceiver: 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'
  ehOwner: 'f526a384-b230-433a-b45c-95f59c4a2dec'
  sbSender: '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39'
  sbReceiver: '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0'
  sbOwner: '090c5cfd-751d-490a-894a-3ce6f1109419'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  blobOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  queueContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  tableContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  wpsOwner: '12cf5a90-567b-43ae-8102-96cf46c7d9b4'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  speechUser: 'f2dc8367-1007-4938-bd23-fe263f013447'
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    logAnalyticsName: 'log-${resourceToken}'
    appInsightsName: 'appi-${resourceToken}'
    publisherPrincipals: concat(
      map([ ids.api, ids.interpreter, ids.storyteller, ids.simulator, ids.functions ], i => { principalId: i.principalId, principalType: svc }),
      user)
  }
}

module data 'modules/data.bicep' = {
  name: 'data'
  scope: rg
  params: {
    location: location
    cosmosLocation: empty(cosmosLocation) ? location : cosmosLocation
    tags: tags
    cosmosName: 'cosmos-${resourceToken}'
    storageName: 'st${resourceToken}'
    cosmosDataPrincipals: concat(
      [ ids.api.principalId, ids.interpreter.principalId, ids.storyteller.principalId, ids.functions.principalId, ids.simulator.principalId ],
      hasUser ? [ principalId ] : [])
    storageRoles: concat(
      [
        { principalId: ids.interpreter.principalId, roleId: roles.blobContributor, principalType: svc }
        { principalId: ids.storyteller.principalId, roleId: roles.blobContributor, principalType: svc }
        { principalId: ids.simulator.principalId, roleId: roles.blobContributor, principalType: svc }
        { principalId: ids.api.principalId, roleId: roles.blobContributor, principalType: svc }
        { principalId: ids.simulator.principalId, roleId: roles.queueContributor, principalType: svc }
        { principalId: ids.functions.principalId, roleId: roles.blobOwner, principalType: svc }
        { principalId: ids.functions.principalId, roleId: roles.queueContributor, principalType: svc }
        { principalId: ids.functions.principalId, roleId: roles.tableContributor, principalType: svc }
      ],
      hasUser ? [ { principalId: principalId, roleId: roles.blobContributor, principalType: principalType } ] : [])
  }
}

module messaging 'modules/messaging.bicep' = {
  name: 'messaging'
  scope: rg
  params: {
    location: location
    tags: tags
    eventHubsName: 'evh-${resourceToken}'
    serviceBusName: 'sb-${resourceToken}'
    eventHubRoles: concat(
      [
        { principalId: ids.simulator.principalId, roleId: roles.ehSender, principalType: svc }
        { principalId: ids.interpreter.principalId, roleId: roles.ehReceiver, principalType: svc }
      ],
      hasUser ? [ { principalId: principalId, roleId: roles.ehOwner, principalType: principalType } ] : [])
    serviceBusRoles: concat(
      [
        { principalId: ids.interpreter.principalId, roleId: roles.sbSender, principalType: svc }
        { principalId: ids.storyteller.principalId, roleId: roles.sbReceiver, principalType: svc }
      ],
      hasUser ? [ { principalId: principalId, roleId: roles.sbOwner, principalType: principalType } ] : [])
  }
}

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  scope: rg
  params: {
    location: location
    tags: tags
    accountName: 'ai-${resourceToken}'
    deployments: [
      { name: 'gpt-5-mini', model: 'gpt-5-mini', version: '2025-08-07', sku: 'GlobalStandard', capacity: 100 }
      { name: 'gpt-4.1-mini', model: 'gpt-4.1-mini', version: '2025-04-14', sku: 'GlobalStandard', capacity: 50 }
    ]
    roles: concat(
      [
        { principalId: ids.storyteller.principalId, roleId: roles.openAiUser, principalType: svc }
        { principalId: ids.storyteller.principalId, roleId: roles.foundryUser, principalType: svc }
        { principalId: ids.api.principalId, roleId: roles.foundryUser, principalType: svc }
        { principalId: ids.storyteller.principalId, roleId: roles.speechUser, principalType: svc }
      ],
      hasUser ? [
        { principalId: principalId, roleId: roles.speechUser, principalType: principalType }
        { principalId: principalId, roleId: roles.openAiUser, principalType: principalType }
        { principalId: principalId, roleId: roles.foundryUser, principalType: principalType }
      ] : [])
  }
}

module realtime 'modules/realtime.bicep' = {
  name: 'realtime'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'wps-${resourceToken}'
    sku: webPubSubSku
    roles: concat(
      map([ ids.api, ids.interpreter, ids.functions ], i => { principalId: i.principalId, roleId: roles.wpsOwner, principalType: svc }),
      hasUser ? [ { principalId: principalId, roleId: roles.wpsOwner, principalType: principalType } ] : [])
  }
}

module registry 'modules/registry.bicep' = {
  name: 'registry'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'cr${resourceToken}'
    pullPrincipals: [ ids.api.principalId, ids.interpreter.principalId, ids.storyteller.principalId, ids.simulator.principalId ]
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'kv-${resourceToken}'
    roles: concat(
      [ { principalId: ids.api.principalId, roleId: '4633458b-17de-408a-b874-0445c86b69e6', principalType: svc } ],
      hasUser ? [ { principalId: principalId, roleId: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7', principalType: principalType } ] : [])
  }
}

module lifecycle 'modules/lifecycle.bicep' = {
  name: 'lifecycle'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    storageName: data.outputs.storageName
    senderPrincipals: concat(
      map([ ids.interpreter, ids.storyteller ], i => { principalId: i.principalId, principalType: svc }),
      user)
  }
}

module web 'modules/web.bicep' = {
  name: 'web'
  scope: rg
  params: { location: location, tags: tags, name: 'swa-${resourceToken}' }
}

var commonEnv = [
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.appInsightsConnectionString }
  { name: 'EVENTHUB_FQDN', value: messaging.outputs.eventHubsFqdn }
  { name: 'EVENTHUB_NAME', value: 'match-stream' }
  { name: 'EVENTHUB_CONSUMER_GROUP', value: 'interpreter' }
  { name: 'SERVICEBUS_FQDN', value: messaging.outputs.serviceBusFqdn }
  { name: 'SERVICEBUS_QUEUE', value: 'moments' }
  { name: 'STORAGE_BLOB_ENDPOINT', value: data.outputs.storageBlobEndpoint }
  { name: 'CHECKPOINT_CONTAINER', value: 'ehcheckpoints' }
  { name: 'COSMOS_ENDPOINT', value: data.outputs.cosmosEndpoint }
  { name: 'COSMOS_DATABASE', value: 'matchlens' }
  { name: 'WEBPUBSUB_ENDPOINT', value: realtime.outputs.endpoint }
  { name: 'WEBPUBSUB_HUB', value: 'matchlens' }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundry.outputs.projectEndpoint }
  { name: 'FOUNDRY_DEPLOYMENTS', value: 'gpt-5-mini,gpt-4.1-mini' }
  { name: 'AZURE_SUBSCRIPTION_ID', value: subscription().subscriptionId }
  { name: 'AZURE_RESOURCE_GROUP', value: rg.name }
  { name: 'SIM_JOB_NAME', value: 'job-sim-${resourceToken}' }
  { name: 'KEYVAULT_URI', value: keyvault.outputs.uri }
  { name: 'MCP_URL', value: 'https://func-cues-${resourceToken}.azurewebsites.net/runtime/webhooks/mcp' }
  { name: 'ASK_DEPLOYMENT', value: 'gpt-4.1-mini' }
  { name: 'SPEECH_RESOURCE_ID', value: foundry.outputs.accountId }
  { name: 'SPEECH_REGION', value: location }
  { name: 'EVENTGRID_ENDPOINT', value: lifecycle.outputs.topicEndpoint }
  { name: 'REPLAY_QUEUE', value: 'replay-requests' }
]

module aca 'modules/aca.bicep' = {
  name: 'aca'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    logAnalyticsName: monitoring.outputs.logAnalyticsName
    registryServer: registry.outputs.loginServer
    identities: ids
    commonEnv: commonEnv
    apiExists: apiExists
    interpreterExists: interpreterExists
    storytellerExists: storytellerExists
    eventHubsName: messaging.outputs.eventHubsName
    serviceBusName: messaging.outputs.serviceBusName
    storageName: data.outputs.storageName
    corsOrigins: [ 'https://${web.outputs.hostname}', 'http://localhost:5173' ]
    demoMode: demoMode
  }
}

module functions 'modules/functions.bicep' = {
  name: 'functions'
  scope: rg
  params: {
    location: location
    tags: tags
    name: 'func-cues-${resourceToken}'
    planName: 'plan-cues-${resourceToken}'
    identityId: ids.functions.id
    identityClientId: ids.functions.clientId
    storageName: data.outputs.storageName
    storageBlobEndpoint: data.outputs.storageBlobEndpoint
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    cosmosEndpoint: data.outputs.cosmosEndpoint
    webPubSubEndpoint: realtime.outputs.endpoint
    alwaysReady: demoMode ? 1 : 0
  }
}

module ops 'modules/ops.bicep' = {
  name: 'ops'
  scope: rg
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    appInsightsId: monitoring.outputs.appInsightsId
    apiUrl: 'https://${aca.outputs.apiFqdn}'
    simulatorJobId: aca.outputs.simulatorJobId
    alertEmail: alertEmail
    keepLive: demoMode
  }
}

// azd reads these into .azure/<env>/.env; local dev uses the same names.
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output AZURE_CONTAINER_ENVIRONMENT_NAME string = aca.outputs.environmentName
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
output EVENTHUB_FQDN string = messaging.outputs.eventHubsFqdn
output EVENTHUB_NAME string = 'match-stream'
output SERVICEBUS_FQDN string = messaging.outputs.serviceBusFqdn
output STORAGE_BLOB_ENDPOINT string = data.outputs.storageBlobEndpoint
output COSMOS_ENDPOINT string = data.outputs.cosmosEndpoint
output WEBPUBSUB_ENDPOINT string = realtime.outputs.endpoint
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output API_URL string = 'https://${aca.outputs.apiFqdn}'
output WEB_URL string = 'https://${web.outputs.hostname}'
output SIM_JOB_NAME string = aca.outputs.simulatorJobName
output INTERPRETER_APP_NAME string = aca.outputs.interpreterName
output FUNCTIONS_APP_NAME string = functions.outputs.name
output KEYVAULT_NAME string = keyvault.outputs.name
output REPLAY_JOB_NAME string = aca.outputs.replayJobName
