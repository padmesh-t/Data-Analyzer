"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import type { QueryResult } from "@/types/api"

export interface LiveWidgetData {
  results: QueryResult
  error?: string
}

interface WsMessage {
  type: string
  widget_id?: number
  results?: QueryResult
  error?: string
  message?: string
  dashboard_id?: number
  active_pollers?: number[]
  status?: string
}

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8090"
const MAX_RECONNECT_DELAY = 30_000
const INITIAL_RECONNECT_DELAY = 1_000
const PING_INTERVAL = 25_000

export function useDashboardWs(dashboardId: number | null) {
  const [connected, setConnected] = useState(false)
  const [liveData, setLiveData] = useState<Map<number, LiveWidgetData>>(new Map())
  const [activePollers, setActivePollers] = useState<number[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const pingTimerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const reconnectDelayRef = useRef(INITIAL_RECONNECT_DELAY)
  const mountedRef = useRef(true)

  const getToken = useCallback(() => {
    if (typeof window === "undefined") return null
    return localStorage.getItem("access_token")
  }, [])

  const connect = useCallback(() => {
    if (!dashboardId) return
    const token = getToken()
    if (!token) return

    const url = `${WS_URL}/ws/dashboards/${dashboardId}?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return }
      setConnected(true)
      reconnectDelayRef.current = INITIAL_RECONNECT_DELAY
      pingTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }))
        }
      }, PING_INTERVAL)
    }

    ws.onmessage = (event) => {
      if (!mountedRef.current) return
      try {
        const msg: WsMessage = JSON.parse(event.data)
        switch (msg.type) {
          case "connected":
            setActivePollers(msg.active_pollers || [])
            break
          case "widget_update":
            if (msg.widget_id && msg.results) {
              setLiveData((prev) => {
                const next = new Map(prev)
                next.set(msg.widget_id!, { results: msg.results! })
                return next
              })
            }
            break
          case "widget_error":
            if (msg.widget_id && msg.error) {
              setLiveData((prev) => {
                const next = new Map(prev)
                const existing = next.get(msg.widget_id!)
                if (existing) {
                  next.set(msg.widget_id!, { ...existing, error: msg.error })
                }
                return next
              })
            }
            break
          case "refresh_all":
          case "refresh_widget":
            // confirmation only, no state change needed
            break
          case "pong":
            break
        }
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      setConnected(false)
      if (pingTimerRef.current) clearInterval(pingTimerRef.current)
      const delay = reconnectDelayRef.current
      reconnectDelayRef.current = Math.min(delay * 2, MAX_RECONNECT_DELAY)
      reconnectTimerRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      // onclose will fire after onerror, so reconnection is handled there
    }
  }, [dashboardId, getToken])

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (pingTimerRef.current) clearInterval(pingTimerRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
      }
    }
  }, [connect])

  const refreshWidget = useCallback((widgetId: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "refresh_widget", widget_id: widgetId }))
    }
  }, [])

  const refreshAll = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "refresh_all" }))
    }
  }, [])

  return { connected, liveData, activePollers, refreshWidget, refreshAll }
}
