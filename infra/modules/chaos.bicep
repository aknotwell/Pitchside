@description('Chaos Studio drill: disable the match-stream Event Hub for 60 s while matches are live.')
param location string
param tags object
param resourceToken string
param eventHubsName string

resource eh 'Microsoft.EventHub/namespaces@2024-01-01' existing = {
  name: eventHubsName
}

resource target 'Microsoft.Chaos/targets@2025-01-01' = {
  name: 'Microsoft-EventHub'
  scope: eh
  properties: {}
}

resource capability 'Microsoft.Chaos/targets/capabilities@2025-01-01' = {
  parent: target
  name: 'ChangeEventHubState-1.0'
}

resource experiment 'Microsoft.Chaos/experiments@2025-01-01' = {
  name: 'chaos-broker-outage-${resourceToken}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    selectors: [
      { type: 'List', id: 'eventhub', targets: [ { type: 'ChaosTarget', id: target.id } ] }
    ]
    steps: [
      {
        name: 'Broker outage: disable match-stream'
        branches: [
          {
            name: 'disable'
            actions: [
              {
                type: 'discrete'
                name: 'urn:csci:microsoft:eventHub:changeEventHubState/1.0'
                selectorId: 'eventhub'
                parameters: [
                  { key: 'desiredState', value: 'Disabled' }
                  { key: 'eventHubs', value: '["match-stream"]' }
                ]
              }
              { type: 'delay', name: 'urn:csci:microsoft:chaosStudio:TimedDelay/1.0', duration: 'PT1M' }
            ]
          }
        ]
      }
      {
        name: 'Recover: re-enable match-stream'
        branches: [
          {
            name: 'enable'
            actions: [
              {
                type: 'discrete'
                name: 'urn:csci:microsoft:eventHub:changeEventHubState/1.0'
                selectorId: 'eventhub'
                parameters: [
                  { key: 'desiredState', value: 'Active' }
                  { key: 'eventHubs', value: '["match-stream"]' }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
  dependsOn: [ capability ]
}

// The experiment's identity may change the hub's state (Event Hubs Data Owner on the namespace).
resource experimentRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: eh
  name: guid(eh.id, experiment.id, 'chaos-eh-owner')
  properties: {
    principalId: experiment.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'f526a384-b230-433a-b45c-95f59c4a2dec')
    principalType: 'ServicePrincipal'
  }
}

// Changing a hub's state is a management-plane write: Contributor, scoped to this namespace only.
resource experimentWrite 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: eh
  name: guid(eh.id, experiment.id, 'chaos-eh-contributor')
  properties: {
    principalId: experiment.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
    principalType: 'ServicePrincipal'
  }
}

output experimentName string = experiment.name
