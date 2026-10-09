param location string
param tags object
param name string
param pullPrincipals array

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in pullPrincipals: {
  scope: acr
  name: guid(acr.id, p, 'acrpull')
  properties: {
    principalId: p
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalType: 'ServicePrincipal'
  }
}]

output loginServer string = acr.properties.loginServer
output name string = acr.name
