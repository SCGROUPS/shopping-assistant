import type { Page } from '@playwright/test'

export async function installVoiceMock(page: Page) {
  await page.addInitScript(() => {
    type VoiceWindow = Window & {
      __voiceTranscript?: string
      __spokenText?: string
    }

    const voiceWindow = window as VoiceWindow

    class MockSpeechRecognition {
      continuous = false
      interimResults = false
      lang = 'en-US'
      onstart: (() => void) | null = null
      onresult: ((event: unknown) => void) | null = null
      onerror: ((event: unknown) => void) | null = null
      onend: (() => void) | null = null

      start() {
        this.onstart?.()
        window.setTimeout(() => {
          const transcript =
            voiceWindow.__voiceTranscript ??
            'A relaxed family day with food and culture'
          this.onresult?.({
            resultIndex: 0,
            results: {
              0: {
                0: { transcript },
                isFinal: true,
              },
              length: 1,
            },
          })
          this.onend?.()
        }, 20)
      }

      stop() {
        this.onend?.()
      }

      abort() {
        this.onend?.()
      }
    }

    class MockSpeechSynthesisUtterance {
      lang = 'en-US'
      rate = 1
      onend: (() => void) | null = null
      onerror: (() => void) | null = null

      constructor(readonly text: string) {}
    }

    Object.defineProperty(window, 'SpeechRecognition', {
      configurable: true,
      value: MockSpeechRecognition,
    })
    Object.defineProperty(window, 'webkitSpeechRecognition', {
      configurable: true,
      value: MockSpeechRecognition,
    })
    Object.defineProperty(window, 'SpeechSynthesisUtterance', {
      configurable: true,
      value: MockSpeechSynthesisUtterance,
    })
    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: {
        cancel() {},
        speak(utterance: MockSpeechSynthesisUtterance) {
          voiceWindow.__spokenText = utterance.text
          window.setTimeout(() => utterance.onend?.(), 50)
        },
      },
    })
  })
}
