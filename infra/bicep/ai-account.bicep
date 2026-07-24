targetScope = 'resourceGroup'

param aiAccountName string
param location string

resource aiAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: aiAccountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: aiAccountName
    publicNetworkAccess: 'Enabled'
  }
}

output accountName string = aiAccount.name
output endpoint string = aiAccount.properties.endpoints['OpenAI Language Model Instance API']

@secure()
output apiKey string = aiAccount.listKeys().key1
