@description('Log Analytics + workspace-based Application Insights with local (key) auth disabled.')
param location string
param tags object
param logAnalyticsName string
param appInsightsName string
param dailyCapGb int = 1
@description('Principals allowed to publish telemetry (Monitoring Metrics Publisher).')
param publisherPrincipals array = []

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: dailyCapGb }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    DisableLocalAuth: true
  }
}

resource publishers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in publisherPrincipals: {
  scope: appInsights
  name: guid(appInsights.id, p.principalId, 'metrics-publisher')
  properties: {
    principalId: p.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '3913510d-42f4-4e42-8a64-420c390055eb')
    principalType: p.principalType
  }
}]

output logAnalyticsId string = logs.id
output logAnalyticsName string = logs.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
