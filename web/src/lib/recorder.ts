import { useEffect, useRef, useState } from 'react'

// Voice messages, recorded in the browser and sent once to the server for transcription.
// Nothing is kept: the recording lives in memory until it is uploaded.

const TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'] // Chrome/Firefox, Safari
export const MAX_SECONDS = 60
const MIN_SECONDS = 0.7
// Loudest moment (RMS, 0..1) a voice near the microphone passes and a quiet room does not. A recording
// that never gets there is not uploaded: Whisper "hears" words in silence ("Obrigado.").
const SPEECH_LEVEL = 0.015

export const canRecord = () =>
  typeof window !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined'

export type Recording = { blob: Blob } | { error: string }

interface Session {
  recorder: MediaRecorder
  stream: MediaStream
  context: AudioContext
  timer: number
  chunks: Blob[]
  started: number
  peak: number
  cancelled: boolean
}

/** Tap to start, tap to send (or cancel). `level` drives a small meter so you can see you are heard. */
export function useRecorder(onDone: (result: Recording) => void) {
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const session = useRef<Session | null>(null)
  const done = useRef(onDone)

  useEffect(() => {
    done.current = onDone
  })

  useEffect(() => () => cancel(), []) // release the microphone if the page goes away mid-recording

  async function start() {
    if (session.current) return
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    } catch (error) {
      const denied = error instanceof DOMException && error.name === 'NotAllowedError'
      done.current({
        error: denied
          ? 'O navegador não liberou o microfone. Permita o acesso nas configurações do site e tente de novo.'
          : 'Não consegui abrir o microfone.',
      })
      return
    }

    const mimeType = TYPES.find((type) => MediaRecorder.isTypeSupported(type))
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType, audioBitsPerSecond: 32_000 } : undefined)
    const context = new AudioContext()
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024
    context.createMediaStreamSource(stream).connect(analyser)
    const samples = new Float32Array(analyser.fftSize)

    const s: Session = { recorder, stream, context, timer: 0, chunks: [], started: performance.now(), peak: 0, cancelled: false }
    session.current = s

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) s.chunks.push(event.data)
    }
    recorder.onstop = () => {
      window.clearInterval(s.timer)
      stream.getTracks().forEach((track) => track.stop())
      void context.close()
      session.current = null
      setRecording(false)
      setLevel(0)
      if (s.cancelled) return
      if ((performance.now() - s.started) / 1000 < MIN_SECONDS) {
        done.current({ error: 'Áudio curto demais. Toque no microfone, fale e toque em enviar.' })
      } else if (s.peak < SPEECH_LEVEL) {
        done.current({ error: 'Não ouvi nada. Confira o microfone e fale mais perto.' })
      } else {
        done.current({ blob: new Blob(s.chunks, { type: recorder.mimeType || mimeType || 'audio/webm' }) })
      }
    }

    s.timer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples)
      let sum = 0
      for (const value of samples) sum += value * value
      const rms = Math.sqrt(sum / samples.length)
      s.peak = Math.max(s.peak, rms)
      setLevel(rms)
      const elapsed = (performance.now() - s.started) / 1000
      setSeconds(elapsed)
      if (elapsed >= MAX_SECONDS) stop()
    }, 100)

    recorder.start(250)
    setSeconds(0)
    setRecording(true)
  }

  function stop() {
    const s = session.current
    if (s && s.recorder.state !== 'inactive') s.recorder.stop()
  }

  function cancel() {
    const s = session.current
    if (!s) return
    s.cancelled = true
    stop()
  }

  return { recording, seconds, level, start, stop, cancel }
}

/** 7 -> "0:07" */
export const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
