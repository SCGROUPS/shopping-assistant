import { Mic, MicOff } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { bcp47 } from '../lib/format'
import { useLocale, useT } from '../lib/useLocale'
import type { Translator } from '../lib/useLocale'

type RecognitionResult = {
  readonly isFinal: boolean
  readonly 0: {
    readonly transcript: string
  }
}

type RecognitionEvent = Event & {
  readonly resultIndex: number
  readonly results: {
    readonly length: number
    readonly [index: number]: RecognitionResult
  }
}

type RecognitionErrorEvent = Event & {
  readonly error: string
}

type Recognition = {
  continuous: boolean
  interimResults: boolean
  lang: string
  onstart: (() => void) | null
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: RecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type RecognitionConstructor = new () => Recognition

declare global {
  interface Window {
    SpeechRecognition?: RecognitionConstructor
    webkitSpeechRecognition?: RecognitionConstructor
  }
}

type VoiceInputButtonProps = {
  onTranscript: (transcript: string, final: boolean) => void
  disabled?: boolean
  label: string
  stopLabel: string
}

const recognitionErrorMessage = (error: string, t: Translator) => {
  if (error === 'not-allowed' || error === 'service-not-allowed') {
    return t('voice.error.blocked')
  }
  if (error === 'no-speech') {
    return t('voice.error.noSpeech')
  }
  return t('voice.error.unavailable')
}

export function VoiceInputButton({
  onTranscript,
  disabled = false,
  label,
  stopLabel,
}: VoiceInputButtonProps) {
  const { locale } = useLocale()
  const t = useT()
  const [listening, setListening] = useState(false)
  const [status, setStatus] = useState('')
  const recognitionRef = useRef<Recognition | null>(null)
  const onTranscriptRef = useRef(onTranscript)
  const supported =
    typeof window !== 'undefined' &&
    Boolean(window.SpeechRecognition ?? window.webkitSpeechRecognition)

  useEffect(() => {
    onTranscriptRef.current = onTranscript
  }, [onTranscript])

  useEffect(
    () => () => {
      recognitionRef.current?.abort()
    },
    [],
  )

  if (!supported) return null

  const toggleListening = () => {
    if (listening) {
      recognitionRef.current?.stop()
      return
    }

    const Constructor =
      window.SpeechRecognition ?? window.webkitSpeechRecognition
    if (!Constructor) return

    const recognition = new Constructor()
    recognition.continuous = false
    recognition.interimResults = true
    // Transcribing a Vietnamese speaker with an en-US recogniser returns
    // nonsense, so the recogniser follows the shopper's language.
    recognition.lang = bcp47(locale)
    recognition.onstart = () => {
      setListening(true)
      setStatus(t('voice.listening'))
    }
    recognition.onresult = (event) => {
      let transcript = ''
      let final = false
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        transcript += event.results[index][0].transcript
        final ||= event.results[index].isFinal
      }
      const normalized = transcript.trim()
      if (normalized) onTranscriptRef.current(normalized, final)
      setStatus(final ? `Heard: ${normalized}` : `Listening: ${normalized}`)
    }
    recognition.onerror = (event) => {
      setStatus(recognitionErrorMessage(event.error, t))
    }
    recognition.onend = () => {
      setListening(false)
      recognitionRef.current = null
    }
    recognitionRef.current = recognition

    try {
      recognition.start()
    } catch {
      setListening(false)
      setStatus(t('voice.alreadyActive'))
    }
  }

  const accessibleLabel = listening ? stopLabel : label

  return (
    <>
      <button
        type="button"
        className={`voice-input-button ${listening ? 'listening' : ''}`}
        onClick={toggleListening}
        disabled={disabled}
        aria-label={accessibleLabel}
        aria-pressed={listening}
        title={accessibleLabel}
      >
        {listening ? <MicOff size={17} /> : <Mic size={17} />}
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {status}
      </span>
    </>
  )
}
