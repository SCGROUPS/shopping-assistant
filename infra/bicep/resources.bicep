param prefix string
param location string
param postgresLocation string
param containerImage string
param buildRevision string

@secure()
param postgresAdminPassword string

param aiEndpoint string
@secure()
param aiApiKey string
param chatDeployment string
param intentDeployment string
param embeddingDeployment string
param imageDeployment string

// Without this the operator console has no first credential and nobody can
// create one, because creating operators itself requires an operator. Left
// empty the storefront runs exactly as before and the console is unreachable,
// which is the safe default for an unattended deploy.
@secure()
param adminBootstrapKey string = ''

@description('Where intent-extraction alerts are sent. Empty means the alerts still fire and are visible in Azure Monitor, but nobody is told.')
param alertEmail string = ''

var normalizedPrefix = toLower(replace(prefix, '-', ''))
var postgresAdmin = 'vietraadmin'
var databaseName = 'vietra'
var registryName = take('${normalizedPrefix}vietra', 50)
var postgresName = take('${normalizedPrefix}-${replace(postgresLocation, ' ', '')}-pg', 63)
var logsName = 'log-${prefix}'
var appInsightsName = 'appi-${prefix}'
var environmentName = 'cae-${prefix}'
var appName = 'ca-${prefix}-web'

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: postgresName
  location: postgresLocation
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '17'
    administratorLogin: postgresAdmin
    administratorLoginPassword: postgresAdminPassword
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    storage: {
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
  }
}

resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    source: 'user-override'
    value: 'VECTOR,PG_TRGM,UNACCENT'
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
  dependsOn: [
    extensions
  ]
}

var databaseUrl = 'postgresql+psycopg://${postgresAdmin}:${uriComponent(postgresAdminPassword)}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logsName
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    Flow_Type: 'Bluefield'
    IngestionMode: 'LogAnalytics'
    WorkspaceResourceId: logs.id
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    environmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        allowInsecure: false
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: contains(containerImage, registry.properties.loginServer)
        ? [
            {
              server: registry.properties.loginServer
              username: registry.listCredentials().username
              passwordSecretRef: 'registry-password'
            }
          ]
        : []
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'registry-password'
          value: registry.listCredentials().passwords[0].value
        }
        {
          name: 'azure-openai-api-key'
          value: aiApiKey
        }
        {
          name: 'admin-bootstrap-key'
          value: adminBootstrapKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'vietra'
          image: containerImage
          env: [
            {
              name: 'APP_ENV'
              value: 'azure'
            }
            {
              name: 'BUILD_REVISION'
              value: buildRevision
            }
            {
              name: 'DEMO_MODE'
              value: 'false'
            }
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'ADMIN_BOOTSTRAP_KEY'
              secretRef: 'admin-bootstrap-key'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: aiEndpoint
            }
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'azure-openai-api-key'
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
              value: chatDeployment
            }
            {
              name: 'AZURE_OPENAI_INTENT_DEPLOYMENT'
              value: intentDeployment
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: embeddingDeployment
            }
            {
              name: 'AZURE_OPENAI_IMAGE_DEPLOYMENT'
              value: imageDeployment
            }
            {
              name: 'AZURE_OPENAI_API_VERSION'
              value: '2025-04-01-preview'
            }
            {
              name: 'OPENAI_EMBEDDING_DIMENSIONS'
              value: '512'
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
    workloadProfileName: 'Consumption'
  }
  dependsOn: [
    database
  ]
}

resource embeddingJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-${prefix}-catalog'
  location: location
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      // Long enough for a cold first backfill: seed, availability, import and
      // then a document per locale for the whole catalogue.
      replicaTimeout: 3600
      // Zero, so one execution means one replica. With a retry, an execution
      // can outlive the deployment script's wait by a whole replica timeout
      // while still looking like it might succeed - and the deployment fails
      // for a job that is, at that moment, running perfectly well.
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: contains(containerImage, registry.properties.loginServer)
        ? [
            {
              server: registry.properties.loginServer
              username: registry.listCredentials().username
              passwordSecretRef: 'registry-password'
            }
          ]
        : []
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'registry-password'
          value: registry.listCredentials().passwords[0].value
        }
        {
          name: 'azure-openai-api-key'
          value: aiApiKey
        }
        {
          name: 'admin-bootstrap-key'
          value: adminBootstrapKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'catalog'
          image: containerImage
          command: [
            'sh'
            '-c'
          ]
          args: [
            // Each step is timed. This job is fourteen minutes of a
            // twenty-nine minute deploy and reported nothing about where they
            // went, so every proposal to speed it up was a guess. `step` runs
            // one CLI command and prints its duration; the deploy log then
            // attributes the time without anyone having to reproduce it.
            'step() { l="$1"; shift; s=$(date -u +%s); echo "--> $l"; "$@"; r=$?; echo "==> $l finished in $(( $(date -u +%s) - s ))s"; return $r; }; cli() { uv run --no-sync python -m app.catalog.cli "$@"; }; step seed-db cli seed-db && step refresh-availability cli refresh-availability && { step import-trippass cli import-trippass || echo "Trippass import skipped: supplier feed unavailable"; } && step enqueue-translations cli enqueue-translations && step reindex cli reindex'
          ]
          env: [
            {
              name: 'APP_ENV'
              value: 'azure'
            }
            {
              name: 'DEMO_MODE'
              value: 'false'
            }
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: aiEndpoint
            }
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'azure-openai-api-key'
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: embeddingDeployment
            }
            {
              name: 'AZURE_OPENAI_API_VERSION'
              value: '2025-04-01-preview'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
    workloadProfileName: 'Consumption'
  }
}

// Translation is its own job, and deliberately not part of the deployment's
// critical path. The first backfill is roughly ten thousand model calls; at
// any concurrency that finishes, it is still tens of minutes of provider
// latency that a deployment must not wait on and must not fail for.
//
// Scheduled rather than manual so translations actually happen without anybody
// remembering to trigger them, and safe to overlap: jobs are leased with
// SKIP LOCKED, so a second replica takes different work rather than the same
// work twice. `--revive` gives up-to-date failed jobs their attempts back, so
// a provider outage costs a delay rather than a permanent gap.
resource translateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-${prefix}-translate'
  location: location
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        // Every two hours. Each run drains what it can and stops when nothing
        // moves forward or the budget is spent, so progress is resumable and
        // an overrun costs a delay rather than a duplicate.
        cronExpression: '0 */2 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaTimeout: 3000
      replicaRetryLimit: 0
      registries: contains(containerImage, registry.properties.loginServer)
        ? [
            {
              server: registry.properties.loginServer
              username: registry.listCredentials().username
              passwordSecretRef: 'registry-password'
            }
          ]
        : []
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'registry-password'
          value: registry.listCredentials().passwords[0].value
        }
        {
          name: 'azure-openai-api-key'
          value: aiApiKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'translate'
          image: containerImage
          command: [
            'sh'
            '-c'
          ]
          args: [
            'uv run --no-sync python -m app.catalog.cli translate --revive'
          ]
          env: [
            {
              name: 'APP_ENV'
              value: 'azure'
            }
            {
              name: 'DEMO_MODE'
              value: 'false'
            }
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: aiEndpoint
            }
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'azure-openai-api-key'
            }
            {
              name: 'AZURE_OPENAI_API_VERSION'
              value: '2025-04-01-preview'
            }
            {
              // Set here rather than in the application default, because it is
              // a fact about this job against this deployment's quota and not
              // about the software. The storefront shares the code and has no
              // reason to inherit a backfill's concurrency.
              name: 'TRANSLATION_CONCURRENCY'
              value: '12'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
    workloadProfileName: 'Consumption'
  }
}

// Schema migration is its own job, deliberately separate from the catalog job.
// When both lived together the migration ran *after* `deploy_stack` had already
// pointed the container app at the new image, so new code served traffic
// against an old schema for however long Alembic took. Splitting it lets
// deploy.sh update and run this job alone - by image, without touching the
// app - and only promote the app once the expand migration has succeeded.
resource migrateJob 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-${prefix}-migrate'
  location: location
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: contains(containerImage, registry.properties.loginServer)
        ? [
            {
              server: registry.properties.loginServer
              username: registry.listCredentials().username
              passwordSecretRef: 'registry-password'
            }
          ]
        : []
      secrets: [
        {
          name: 'database-url'
          value: databaseUrl
        }
        {
          name: 'registry-password'
          value: registry.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: containerImage
          command: [
            'sh'
            '-c'
          ]
          args: [
            'uv run --no-sync alembic upgrade head'
          ]
          env: [
            {
              name: 'APP_ENV'
              value: 'azure'
            }
            {
              name: 'DATABASE_URL'
              secretRef: 'database-url'
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
    }
    workloadProfileName: 'Consumption'
  }
}

// Both outages were invisible for days: search catches every extraction
// failure and answers HTTP 200 with a full page, so no request failed, no
// availability metric moved, and the health endpoint kept saying "ok". The
// counters added on that surface are only worth having if something reads
// them, and until these rules existed nothing did.
resource alertRecipients 'Microsoft.Insights/actionGroups@2023-01-01' = if (!empty(alertEmail)) {
  name: 'ag-${prefix}-intent'
  location: 'global'
  properties: {
    groupShortName: 'vietraIntent'
    enabled: true
    emailReceivers: [
      {
        name: 'operator'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
  }
}

// Compiles to resourceId(), which is a pure string function, so it stays
// valid when no email was given and the action group was never deployed.
var alertActions = empty(alertEmail) ? [] : [alertRecipients.id]

resource intentFailureAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-${prefix}-intent-extraction-failing'
  location: location
  properties: {
    displayName: 'Vietra: search is not understanding shoppers'
    description: 'The intent extractor threw and the search fell back to deterministic parsing. The shopper still received HTTP 200 and a full page of products, which is why this cannot be detected from request metrics.'
    severity: 1
    enabled: true
    scopes: [
      logs.id
    ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          // Threshold is zero-tolerance rather than a ratio on purpose. At this
          // traffic level a ratio has a denominator of one or two searches, so
          // it swings between 0 and 1 on no real signal - and the provider has
          // already retried twice before this line is ever written, so a single
          // occurrence is a shopper who got an unfiltered page, not a blip.
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == '${appName}'
| where Log_s has 'Intent extraction failed'
| summarize FailedSearches = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'FailedSearches'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: alertActions
    }
  }
}

resource intentProbeAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-${prefix}-intent-probe-rejected'
  location: location
  properties: {
    displayName: 'Vietra: a revision refused to serve'
    description: 'The startup probe found the intent deployment rejecting our requests, so the revision failed its readiness check and took no traffic. With one replica and no automatic rollback this means the storefront is down, which is the deliberate trade - but it is not a state to discover from a customer.'
    severity: 0
    enabled: true
    scopes: [
      logs.id
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      allOf: [
        {
          query: '''
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == '${appName}'
| where Log_s has 'Intent probe' and Log_s has_any ('rejected', 'gave up')
| summarize RejectedProbes = count()
'''
          timeAggregation: 'Total'
          metricMeasureColumn: 'RejectedProbes'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    autoMitigate: true
    actions: {
      actionGroups: alertActions
    }
  }
}

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output containerAppName string = containerApp.name
output containerAppUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output postgresServerName string = postgres.name
