@description('Azure Web PubSub for pushing cues, tracking frames and match state to browsers. Entra-only.')
param location string
param tags object
param name string
@allowed([ 'Free_F1', 'Standard_S1', 'Premium_P1' ])
param sku string = 'Free_F1'
param roles array

resource wps 'Microsoft.SignalRService/webPubSub@2024-03-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: sku, capacity: 1 }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource roleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in roles: {
  scope: wps
  name: guid(wps.id, r.principalId, r.roleId)
  properties: {
    principalId: r.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r.roleId)
    principalType: r.principalType
  }
}]

output endpoint string = 'https://${wps.properties.hostName}'
output name string = wps.name
