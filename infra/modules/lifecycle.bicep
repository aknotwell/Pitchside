@description('Match lifecycle: Event Grid topic (CloudEvents, Entra-only) -> Storage queue -> event-driven replay job.')
param location string
param tags object
param resourceToken string
param storageName string
@description('Principals that publish lifecycle events (EventGrid Data Sender).')
param senderPrincipals array

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageName
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource replayQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01' = {
  parent: queues
  name: 'replay-requests'
}

resource topic 'Microsoft.EventGrid/topics@2025-02-15' = {
  name: 'evgt-lifecycle-${resourceToken}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    inputSchema: 'CloudEventSchemaV1_0'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource senders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in senderPrincipals: {
  scope: topic
  name: guid(topic.id, p.principalId, 'eg-sender')
  properties: {
    principalId: p.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'd5a91429-5739-47e2-a06b-3470a27159e7')
    principalType: p.principalType
  }
}]

// The topic's own identity may enqueue into the replay queue (no keys, no SAS).
resource topicToQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, topic.id, 'queue-sender')
  properties: {
    principalId: topic.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'c6a89b2d-59bc-44d0-9896-0f6e12d7b80a')
    principalType: 'ServicePrincipal'
  }
}

resource fullTimeToReplay 'Microsoft.EventGrid/topics/eventSubscriptions@2025-02-15' = {
  parent: topic
  name: 'fulltime-to-replay'
  properties: {
    eventDeliverySchema: 'CloudEventSchemaV1_0'
    filter: { includedEventTypes: [ 'MatchLens.Match.FullTime' ] }
    deliveryWithResourceIdentity: {
      identity: { type: 'SystemAssigned' }
      destination: {
        endpointType: 'StorageQueue'
        properties: {
          resourceId: storage.id
          queueName: replayQueue.name
          queueMessageTimeToLiveInSeconds: 86400
        }
      }
    }
    retryPolicy: { maxDeliveryAttempts: 10, eventTimeToLiveInMinutes: 1440 }
  }
  dependsOn: [ topicToQueue ]
}

output topicEndpoint string = topic.properties.endpoint
output topicName string = topic.name
