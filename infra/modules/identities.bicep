@description('One user-assigned managed identity per service (least privilege, keyless).')
param location string
param tags object
param resourceToken string

var names = [ 'api', 'interpreter', 'storyteller', 'simulator', 'functions' ]

resource ids 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for n in names: {
  name: 'id-${n}-${resourceToken}'
  location: location
  tags: tags
}]

output identities object = {
  api: { id: ids[0].id, principalId: ids[0].properties.principalId, clientId: ids[0].properties.clientId }
  interpreter: { id: ids[1].id, principalId: ids[1].properties.principalId, clientId: ids[1].properties.clientId }
  storyteller: { id: ids[2].id, principalId: ids[2].properties.principalId, clientId: ids[2].properties.clientId }
  simulator: { id: ids[3].id, principalId: ids[3].properties.principalId, clientId: ids[3].properties.clientId }
  functions: { id: ids[4].id, principalId: ids[4].properties.principalId, clientId: ids[4].properties.clientId }
}
