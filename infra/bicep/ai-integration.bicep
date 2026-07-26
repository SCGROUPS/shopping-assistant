targetScope = 'resourceGroup'

param aiAccountName string
param chatDeployment string
param nanoDeployment string
param embeddingDeployment string
param imageDeployment string

resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: aiAccountName
}

resource chatModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiAccount
  name: chatDeployment
  sku: {
    name: 'GlobalStandard'
    // Sized for the translation backfill, not for the storefront. Eight
    // thousand field-locale pairs at capacity 10 (10K TPM) spend almost the
    // whole run collecting 429s and publish a handful per batch, so a full
    // catalogue would take weeks of two-hourly runs. This is also why the
    // number lives here: raising it only in the portal means the next
    // deployment quietly puts it back.
    capacity: 300
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4-mini'
      version: '2026-03-17'
    }
    raiPolicyName: 'Microsoft.Default'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource nanoModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiAccount
  name: nanoDeployment
  sku: {
    name: 'GlobalStandard'
    capacity: 3200
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5.4-nano'
      version: '2026-03-17'
    }
    raiPolicyName: 'Microsoft.Default'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [
    chatModelDeployment
  ]
}

resource embeddingModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiAccount
  name: embeddingDeployment
  sku: {
    name: 'Standard'
    capacity: 120
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
    raiPolicyName: 'Microsoft.Default'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [
    nanoModelDeployment
  ]
}

resource imageModelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: aiAccount
  name: imageDeployment
  sku: {
    name: 'GlobalStandard'
    capacity: 1
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-image-1-mini'
      version: '2025-10-06'
    }
    raiPolicyName: 'Microsoft.Default'
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [
    embeddingModelDeployment
  ]
}

output imageDeploymentName string = imageModelDeployment.name
