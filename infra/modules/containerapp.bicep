@description('One Container App with a user-assigned identity, ACR pull by identity, and optional KEDA rules.')
param name string
param location string
param tags object
param serviceName string
param environmentId string
param identityId string
param registryServer string
param exists bool
param placeholderImage string = 'mcr.microsoft.com/k8se/quickstart:latest'
param env array
@allowed([ 'external', 'internal', 'none' ])
param ingress string = 'none'
param targetPort int = 8000
param corsOrigins array = []
param minReplicas int = 0
param maxReplicas int = 4
param scaleRules array = []
param cpu string = '0.5'
param memory string = '1Gi'

module fetchImage 'fetch-image.bicep' = {
  name: '${name}-image'
  params: { name: name, exists: exists }
}

var image = exists && !empty(fetchImage.outputs.image) ? fetchImage.outputs.image : placeholderImage

resource app 'Microsoft.App/containerApps@2025-07-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': serviceName })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [ { server: registryServer, identity: identityId } ]
      ingress: ingress == 'none' ? null : {
        external: ingress == 'external'
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
        corsPolicy: empty(corsOrigins) ? null : {
          allowedOrigins: corsOrigins
          allowedMethods: [ 'GET', 'POST', 'OPTIONS' ]
          allowedHeaders: [ '*' ]
          maxAge: 3600
        }
      }
    }
    template: {
      containers: [
        {
          name: 'main'
          image: image
          env: env
          resources: { cpu: json(cpu), memory: memory }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: scaleRules
      }
    }
  }
}

output name string = app.name
output fqdn string = ingress == 'none' ? '' : app.properties.configuration.ingress.fqdn
output image string = image
