@description('Operations: availability test + alert on the API, and a Logic App that keeps a live match on air.')
param location string
param tags object
param resourceToken string
param appInsightsId string
param apiUrl string
param simulatorJobId string
param alertEmail string = ''
@description('Enable the keep-live automation (judging window).')
param keepLive bool = false

resource ag 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: 'ag-matchlens-${resourceToken}'
  location: 'global'
  tags: tags
  properties: {
    groupShortName: 'matchlens'
    enabled: true
    emailReceivers: empty(alertEmail) ? [] : [ { name: 'owner', emailAddress: alertEmail, useCommonAlertSchema: true } ]
  }
}

resource webtest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: 'wt-api-health-${resourceToken}'
  location: location
  tags: union(tags, { 'hidden-link:${appInsightsId}': 'Resource' })
  kind: 'standard'
  properties: {
    SyntheticMonitorId: 'wt-api-health-${resourceToken}'
    Name: 'API health'
    Enabled: true
    Frequency: 300
    Timeout: 30
    Kind: 'standard'
    RetryEnabled: true
    Locations: [ { Id: 'us-va-ash-azr' }, { Id: 'us-tx-sn1-azr' }, { Id: 'emea-nl-ams-azr' } ]
    Request: { RequestUrl: '${apiUrl}/api/health', HttpVerb: 'GET', ParseDependentRequests: false }
    ValidationRules: { ExpectedHttpStatusCode: 200, SSLCheck: true, SSLCertRemainingLifetimeCheck: 7 }
  }
}

resource availabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'alert-api-availability-${resourceToken}'
  location: 'global'
  tags: tags
  properties: {
    description: 'MatchLens API failing from 2+ regions'
    severity: 1
    enabled: true
    scopes: [ webtest.id, appInsightsId ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria'
      webTestId: webtest.id
      componentId: appInsightsId
      failedLocationCount: 2
    }
    actions: [ { actionGroupId: ag.id } ]
  }
}

// Keep a live match on air: every 10 minutes, if nothing is live, start a simulator execution.
resource keepLiveApp 'Microsoft.Logic/workflows@2019-05-01' = {
  name: 'logic-keeplive-${resourceToken}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    state: keepLive ? 'Enabled' : 'Disabled'
    definition: {
      '$schema': 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
      contentVersion: '1.0.0.0'
      triggers: {
        every10min: { type: 'Recurrence', recurrence: { frequency: 'Minute', interval: 10 } }
      }
      actions: {
        listMatches: {
          type: 'Http'
          inputs: { method: 'GET', uri: '${apiUrl}/api/matches' }
        }
        liveMatches: {
          type: 'Query'
          runAfter: { listMatches: [ 'Succeeded' ] }
          inputs: {
            from: '@body(\'listMatches\')?[\'matches\']'
            where: '@and(equals(item()?[\'ended\'], false), greater(item()?[\'updated\'], sub(body(\'listMatches\')?[\'now\'], 300)))'
          }
        }
        ifNothingLive: {
          type: 'If'
          runAfter: { liveMatches: [ 'Succeeded' ] }
          expression: { and: [ { equals: [ '@length(body(\'liveMatches\'))', 0 ] } ] }
          actions: {
            startMatch: {
              type: 'Http'
              inputs: {
                method: 'POST'
                uri: '${environment().resourceManager}${substring(simulatorJobId, 1)}/start?api-version=2025-07-01'
                body: {}
                authentication: { type: 'ManagedServiceIdentity', audience: environment().resourceManager }
              }
            }
          }
          else: { actions: {} }
        }
      }
    }
  }
}

resource job 'Microsoft.App/jobs@2025-07-01' existing = {
  name: last(split(simulatorJobId, '/'))
}

resource logicCanRunJob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: job
  name: guid(job.id, keepLiveApp.id, 'jobs-operator')
  properties: {
    principalId: keepLiveApp.identity.principalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b9a307c4-5aa3-4b52-ba60-2b17c136cd7b')
    principalType: 'ServicePrincipal'
  }
}

output keepLiveName string = keepLiveApp.name
