@description('Event Hubs Standard (match-stream) and Service Bus Standard (moments queue with sessions). Entra-only.')
param location string
param tags object
param eventHubsName string
param serviceBusName string
@description('[{ principalId, roleId, principalType }] on the Event Hubs namespace.')
param eventHubRoles array
@description('[{ principalId, roleId, principalType }] on the Service Bus namespace.')
param serviceBusRoles array

resource eh 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: eventHubsName
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 }
  properties: {
    disableLocalAuth: true
    isAutoInflateEnabled: false
    minimumTlsVersion: '1.2'
  }
}

resource stream 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eh
  name: 'match-stream'
  properties: {
    partitionCount: 4
    messageRetentionInDays: 1
  }
}

resource cgs 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = [for n in [ 'interpreter', 'fabric', 'archive' ]: {
  parent: stream
  name: n
}]

resource ehRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in eventHubRoles: {
  scope: eh
  name: guid(eh.id, r.principalId, r.roleId)
  properties: {
    principalId: r.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r.roleId)
    principalType: r.principalType
  }
}]

resource sb 'Microsoft.ServiceBus/namespaces@2024-01-01' = {
  name: serviceBusName
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
  }
}

resource moments 'Microsoft.ServiceBus/namespaces/queues@2024-01-01' = {
  parent: sb
  name: 'moments'
  properties: {
    requiresSession: true
    maxDeliveryCount: 5
    lockDuration: 'PT1M'
    defaultMessageTimeToLive: 'PT2H'
    deadLetteringOnMessageExpiration: true
  }
}

resource sbRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in serviceBusRoles: {
  scope: sb
  name: guid(sb.id, r.principalId, r.roleId)
  properties: {
    principalId: r.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r.roleId)
    principalType: r.principalType
  }
}]

output eventHubsName string = eh.name
output eventHubsFqdn string = '${eh.name}.servicebus.windows.net'
output serviceBusName string = sb.name
output serviceBusFqdn string = '${sb.name}.servicebus.windows.net'
