@description('Cosmos DB (serverless, Entra-only) and a Storage account (no shared keys).')
param location string
param cosmosLocation string
param tags object
param cosmosName string
param storageName string
@description('Principals that get the Cosmos DB Built-in Data Contributor role.')
param cosmosDataPrincipals array
@description('[{ principalId, roleId, principalType }] assigned on the storage account.')
param storageRoles array

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosName
  location: cosmosLocation
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [ { locationName: cosmosLocation, failoverPriority: 0, isZoneRedundant: false } ]
    capabilities: [ { name: 'EnableServerless' } ]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
  }
}

resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: 'matchlens'
  properties: { resource: { id: 'matchlens' } }
}

// A finished match's state doc expires 30 days after the whistle (FINISHED_STATE_TTL_S in
// interpreter.py). Its cues, moments and runs are written earlier, so they keep 31 days: a match
// still in the list never loses its goals, overlays or recap.
var matchDataTtl = 31 * 86400
var containers = [
  { name: 'matchState', pk: '/matchId', ttl: -1 }
  { name: 'cues', pk: '/matchId', ttl: matchDataTtl }
  { name: 'moments', pk: '/matchId', ttl: matchDataTtl }
  { name: 'runs', pk: '/matchId', ttl: matchDataTtl }
  { name: 'checkpoints', pk: '/workflow_name', ttl: 86400 }
  { name: 'leases', pk: '/id', ttl: -1 }
]

resource cont 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [for c in containers: {
  parent: db
  name: c.name
  properties: {
    resource: {
      id: c.name
      partitionKey: { paths: [ c.pk ], kind: 'Hash' }
      defaultTtl: c.ttl
    }
  }
}]

resource cosmosRoles 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for p in cosmosDataPrincipals: {
  parent: cosmos
  name: guid(cosmos.id, p, 'data-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: p
    scope: cosmos.id
  }
}]

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    isHnsEnabled: false
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for n in [ 'ehcheckpoints', 'deployments', 'params', 'replays', 'raw-open-data', 'audio' ]: {
  parent: blobs
  name: n
}]

// Interpreter snapshots are only needed while a match is live; finished ones would pile up forever.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-interpreter-snapshots'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: { blobTypes: [ 'blockBlob' ], prefixMatch: [ 'ehcheckpoints/snapshots/' ] }
            actions: { baseBlob: { delete: { daysAfterModificationGreaterThan: 2 } } }
          }
        }
      ]
    }
  }
}

resource storageRoleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in storageRoles: {
  scope: storage
  name: guid(storage.id, r.principalId, r.roleId)
  properties: {
    principalId: r.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r.roleId)
    principalType: r.principalType
  }
}]

output cosmosEndpoint string = cosmos.properties.documentEndpoint
output cosmosName string = cosmos.name
output storageName string = storage.name
output storageBlobEndpoint string = storage.properties.primaryEndpoints.blob
