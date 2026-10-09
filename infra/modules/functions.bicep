@description('Azure Functions Flex Consumption app (Python): the cue publisher (Cosmos change feed -> Web PubSub).')
param location string
param tags object
param name string
param planName string
param identityId string
param identityClientId string
param storageName string
param storageBlobEndpoint string
param appInsightsConnectionString string
param cosmosEndpoint string
param webPubSubEndpoint string
param alwaysReady int = 0

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2024-04-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': 'cue-publisher' })
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storageBlobEndpoint}deployments'
          authentication: { type: 'UserAssignedIdentity', userAssignedIdentityResourceId: identityId }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
        alwaysReady: alwaysReady > 0 ? [ { name: 'function:cue_publisher', instanceCount: alwaysReady } ] : []
      }
      runtime: { name: 'python', version: '3.13' }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storageName }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: identityClientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'ClientId=${identityClientId};Authorization=AAD' }
        { name: 'CosmosDb__accountEndpoint', value: cosmosEndpoint }
        { name: 'CosmosDb__credential', value: 'managedidentity' }
        { name: 'CosmosDb__clientId', value: identityClientId }
        { name: 'COSMOS_DATABASE', value: 'matchlens' }
        { name: 'WEBPUBSUB_ENDPOINT', value: webPubSubEndpoint }
        { name: 'WEBPUBSUB_HUB', value: 'matchlens' }
        { name: 'AZURE_CLIENT_ID', value: identityClientId }
      ]
    }
  }
}

output name string = app.name
output hostname string = app.properties.defaultHostName
