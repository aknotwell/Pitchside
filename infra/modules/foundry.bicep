@description('Microsoft Foundry resource + project with pinned model deployments. Entra-only.')
param location string
param tags object
param accountName string
param projectName string = 'matchlens'
@description('[{ name, model, version, sku, capacity }] deployed one after another.')
param deployments array
@description('[{ principalId, roleId, principalType }] on the Foundry resource.')
param roles array

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'MatchLens'
    description: 'Explainable match intelligence on synthetic football'
  }
}

@batchSize(1)
resource deps 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for d in deployments: {
  parent: account
  name: d.name
  sku: { name: d.sku, capacity: d.capacity }
  properties: {
    model: { format: 'OpenAI', name: d.model, version: d.version }
    versionUpgradeOption: 'NoAutoUpgrade'
  }
  dependsOn: [ project ]
}]

resource roleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in roles: {
  scope: account
  name: guid(account.id, r.principalId, r.roleId)
  properties: {
    principalId: r.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r.roleId)
    principalType: r.principalType
  }
}]

output accountName string = account.name
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output openAiEndpoint string = 'https://${account.name}.openai.azure.com/'
