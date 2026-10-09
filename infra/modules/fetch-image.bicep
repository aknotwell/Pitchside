@description('Reads the image a Container App is currently running (so re-provisioning never rolls back a deploy).')
param name string
param exists bool

resource existingApp 'Microsoft.App/containerApps@2025-07-01' existing = if (exists) {
  name: name
}

output image string = exists ? existingApp!.properties.template.containers[0].image : ''
