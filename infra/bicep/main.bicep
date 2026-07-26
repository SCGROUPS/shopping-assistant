targetScope = 'subscription'

@description('Short globally unique prefix used for Azure resource names.')
@minLength(4)
@maxLength(12)
param prefix string

@description('Azure region for the application resources.')
param location string = 'eastus2'

@description('PostgreSQL region. This subscription currently restricts PostgreSQL creation in East US and East US 2.')
param postgresLocation string = 'centralus'

@description('Resource group to create.')
param resourceGroupName string = 'rg-${prefix}-poc'

@description('Globally unique Azure AI Services account name.')
param aiAccountName string = take('ai${uniqueString(subscription().id, prefix)}', 24)

@description('Container image to deploy after it has been pushed to the new registry.')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Changes on each rollout to force Container Apps to pull a rebuilt image tag.')
param buildRevision string = 'bootstrap'

@secure()
@description('Initial PostgreSQL administrator password.')
param postgresAdminPassword string

@secure()
@description('First operator credential for the console. Empty leaves the console unreachable, which is the safe default for an unattended deploy.')
param adminBootstrapKey string = ''
param alertEmail string = ''

param chatDeployment string = 'gpt-5.4-mini'
// Intent extraction shares the chat model. Nano's output wandered between
// identical calls - inventing categories this catalogue does not stock, and
// moving the destination in and out of the hard constraints - which showed up
// as a search that returned nothing about half the time. Its capacity was also
// 10 against the chat model's 300.
param intentDeployment string = 'gpt-5.4-mini'

// Declared so the deployment is managed rather than drifting. Nothing points
// at it today - intent moved to the chat model - but it exists in the account
// and infrastructure that does not describe reality is worse than none.
param nanoDeployment string = 'gpt-5.4-nano'
param embeddingDeployment string = 'text-embedding-3-small'
param imageDeployment string = 'gpt-image-1-mini'

module aiAccount 'ai-account.bicep' = {
  name: 'vietra-ai-account'
  scope: rg
  params: {
    aiAccountName: aiAccountName
    location: location
  }
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: {
    application: 'vietra'
    environment: 'poc'
    workload: 'intelligent-tourism-commerce'
  }
}

module resources 'resources.bicep' = {
  name: 'vietra-resources'
  scope: rg
  params: {
    prefix: prefix
    location: location
    postgresLocation: postgresLocation
    containerImage: containerImage
    buildRevision: buildRevision
    postgresAdminPassword: postgresAdminPassword
    chatDeployment: chatDeployment
    intentDeployment: intentDeployment
    embeddingDeployment: embeddingDeployment
    imageDeployment: imageDeployment
    aiEndpoint: aiAccount.outputs.endpoint
    aiApiKey: aiAccount.outputs.apiKey
    adminBootstrapKey: adminBootstrapKey
    alertEmail: alertEmail
  }
}

module aiIntegration 'ai-integration.bicep' = {
  name: 'vietra-ai-integration'
  scope: rg
  params: {
    aiAccountName: aiAccount.outputs.accountName
    chatDeployment: chatDeployment
    nanoDeployment: nanoDeployment
    embeddingDeployment: embeddingDeployment
    imageDeployment: imageDeployment
  }
}

output resourceGroupName string = rg.name
output registryName string = resources.outputs.registryName
output registryLoginServer string = resources.outputs.registryLoginServer
output containerAppName string = resources.outputs.containerAppName
output containerAppUrl string = resources.outputs.containerAppUrl
output postgresServerName string = resources.outputs.postgresServerName
output aiAccountName string = aiAccount.outputs.accountName
