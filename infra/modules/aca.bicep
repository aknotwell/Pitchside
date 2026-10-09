@description('Container Apps environment, the three apps (api, interpreter, storyteller) and the simulator job.')
param location string
param tags object
param resourceToken string
param logAnalyticsName string
param registryServer string
param identities object
param commonEnv array
param apiExists bool
param interpreterExists bool
param storytellerExists bool
param eventHubsName string
param serviceBusName string
param storageName string
param corsOrigins array
param demoMode bool

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

resource env 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: 'cae-${resourceToken}'
  location: location
  tags: tags
  properties: {
    workloadProfiles: [ { name: 'Consumption', workloadProfileType: 'Consumption' } ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var minDemo = demoMode ? 1 : 0

module api 'containerapp.bicep' = {
  name: 'app-api'
  params: {
    name: 'ca-api-${resourceToken}'
    location: location
    tags: tags
    serviceName: 'api'
    environmentId: env.id
    identityId: identities.api.id
    registryServer: registryServer
    exists: apiExists
    ingress: 'external'
    targetPort: 8000
    corsOrigins: corsOrigins
    minReplicas: minDemo
    maxReplicas: 4
    env: concat(commonEnv, [
      { name: 'MATCHLENS_SERVICE', value: 'api' }
      { name: 'AZURE_CLIENT_ID', value: identities.api.clientId }
      { name: 'OTEL_SERVICE_NAME', value: 'api' }
    ])
    scaleRules: [ { name: 'http', http: { metadata: { concurrentRequests: '50' } } } ]
  }
}

module interpreter 'containerapp.bicep' = {
  name: 'app-interpreter'
  params: {
    name: 'ca-interp-${resourceToken}'
    location: location
    tags: tags
    serviceName: 'interpreter'
    environmentId: env.id
    identityId: identities.interpreter.id
    registryServer: registryServer
    exists: interpreterExists
    minReplicas: minDemo
    maxReplicas: 4
    cpu: '1.0'
    memory: '2Gi'
    env: concat(commonEnv, [
      { name: 'MATCHLENS_SERVICE', value: 'interpreter' }
      { name: 'AZURE_CLIENT_ID', value: identities.interpreter.clientId }
      { name: 'OTEL_SERVICE_NAME', value: 'interpreter' }
    ])
    scaleRules: [
      {
        name: 'eventhub-lag'
        custom: {
          type: 'azure-eventhub'
          identity: identities.interpreter.id
          metadata: {
            eventHubNamespace: eventHubsName
            eventHubName: 'match-stream'
            consumerGroup: 'interpreter'
            unprocessedEventThreshold: '200'
            activationUnprocessedEventThreshold: '1'
            storageAccountName: storageName
            blobContainer: 'ehcheckpoints'
            checkpointStrategy: 'blobMetadata'
          }
        }
      }
    ]
  }
}

module storyteller 'containerapp.bicep' = {
  name: 'app-storyteller'
  params: {
    name: 'ca-story-${resourceToken}'
    location: location
    tags: tags
    serviceName: 'storyteller'
    environmentId: env.id
    identityId: identities.storyteller.id
    registryServer: registryServer
    exists: storytellerExists
    minReplicas: minDemo
    maxReplicas: 6
    env: concat(commonEnv, [
      { name: 'MATCHLENS_SERVICE', value: 'storyteller' }
      { name: 'AZURE_CLIENT_ID', value: identities.storyteller.clientId }
      { name: 'OTEL_SERVICE_NAME', value: 'storyteller' }
    ])
    scaleRules: [
      {
        name: 'servicebus-depth'
        custom: {
          type: 'azure-servicebus'
          identity: identities.storyteller.id
          metadata: {
            namespace: serviceBusName
            queueName: 'moments'
            messageCount: '3'
          }
        }
      }
    ]
  }
}

// The simulator runs as a job: one execution = one live match. Parallel executions = load test.
resource simulator 'Microsoft.App/jobs@2025-07-01' = {
  name: 'job-sim-${resourceToken}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identities.simulator.id}': {} }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 10800
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [ { server: registryServer, identity: identities.simulator.id } ]
    }
    template: {
      containers: [
        {
          name: 'main'
          image: interpreter.outputs.image
          env: concat(commonEnv, [
            { name: 'MATCHLENS_SERVICE', value: 'simulator' }
            { name: 'AZURE_CLIENT_ID', value: identities.simulator.clientId }
            { name: 'OTEL_SERVICE_NAME', value: 'simulator' }
            { name: 'SIM_SPEED', value: '1' }
            { name: 'SIM_SEED', value: '0' }
          ])
          resources: { cpu: json('1.0'), memory: '2Gi' }
        }
      ]
    }
  }
}

output environmentName string = env.name
output apiName string = api.outputs.name
output apiFqdn string = api.outputs.fqdn
output interpreterName string = interpreter.outputs.name
output storytellerName string = storyteller.outputs.name
output simulatorJobName string = simulator.name
output simulatorJobId string = simulator.id

// The api may start simulator executions ("start a match" button), nothing else.
resource apiCanRunJob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: simulator
  name: guid(simulator.id, identities.api.principalId, 'jobs-operator')
  properties: {
    principalId: identities.api.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b9a307c4-5aa3-4b52-ba60-2b17c136cd7b')
    principalType: 'ServicePrincipal'
  }
}
