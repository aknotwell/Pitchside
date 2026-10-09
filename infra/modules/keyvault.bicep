@description('Key Vault (RBAC) for the few secrets that are not Entra tokens, e.g. the MCP extension key.')
param location string
param tags object
param name string
@description('[{ principalId, roleId, principalType }]')
param roles array

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource ra 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in roles: {
  scope: kv
  name: guid(kv.id, r.principalId, r.roleId)
  properties: {
    principalId: r.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r.roleId)
    principalType: r.principalType
  }
}]

output name string = kv.name
output uri string = kv.properties.vaultUri
