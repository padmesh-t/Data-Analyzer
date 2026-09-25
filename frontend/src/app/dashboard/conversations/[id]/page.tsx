"use client"

import { useEffect, useState, useRef, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { api } from "@/lib/api-client"
import { useToast } from "@/components/ui/use-toast"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { formatDate } from "@/lib/utils"
import { VisualizationRenderer } from "@/components/visualization/visualization-renderer"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import type { ConversationMessageResponse, DatabaseConnectionResponse } from "@/types/api"
import {
  MessageSquare,
  Send,
  Loader2,
  User,
  Sparkles,
  Trash2,
  ArrowLeft,
  Clock,
  Database,
  AlertCircle,
  BarChart3,
  BookOpen,
  Save,
} from "lucide-react"

function extractSql(content: string): string | null {
  const m = content.match(/```sql\n([\s\S]*?)```/)
  return m ? m[1].trim() : null
}

export default function ConversationDetailPage() {
  const params = useParams()
  const router = useRouter()
  const { toast } = useToast()
  const conversationId = Number(params.id)

  const [messages, setMessages] = useState<ConversationMessageResponse[]>([])
  const [title, setTitle] = useState("Conversation")
  const [databaseId, setDatabaseId] = useState<number | null>(null)
  const [input, setInput] = useState("")
  const [isSending, setIsSending] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [editingTitle, setEditingTitle] = useState(false)
  const [newTitle, setNewTitle] = useState("")
  const [databases, setDatabases] = useState<DatabaseConnectionResponse[]>([])
  const [selectingDb, setSelectingDb] = useState(false)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1)
  const [savingMsg, setSavingMsg] = useState<ConversationMessageResponse | null>(null)
  const [templateTitle, setTemplateTitle] = useState("")
  const [templateDesc, setTemplateDesc] = useState("")
  const [saving, setSaving] = useState(false)
  const suggestRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const suggestTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [])

  useEffect(() => { scrollToBottom() }, [messages, scrollToBottom])

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (suggestRef.current && !suggestRef.current.contains(e.target as Node)) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener("mousedown", handleClick)
    return () => document.removeEventListener("mousedown", handleClick)
  }, [])

  const fetchData = useCallback(async () => {
    try {
      const [convData, msgData, dbs] = await Promise.all([
        api.getConversationById(conversationId),
        api.getConversationMessages(conversationId, { per_page: 100 }),
        api.listDatabases({ per_page: 100 }).catch(() => ({ connections: [] })),
      ])
      setTitle(convData.title || "Conversation")
      setDatabaseId(convData.database_id ?? null)
      setMessages(msgData.messages)
      setDatabases(dbs.connections || [])
    } catch {
      toast({ title: "Error", description: "Failed to load conversation", variant: "destructive" })
      router.push("/dashboard/conversations")
    } finally {
      setIsLoading(false)
    }
  }, [conversationId, router, toast])

  useEffect(() => { fetchData() }, [fetchData])

  const fetchSuggestions = useCallback(async (q: string) => {
    if (q.length < 2) { setShowSuggestions(false); return }
    try {
      const res = await api.querySuggestions(q)
      setSuggestions(res)
      setShowSuggestions(res.length > 0)
      setSelectedSuggestion(-1)
    } catch {
      setShowSuggestions(false)
    }
  }, [])

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setInput(val)
    if (suggestTimeoutRef.current) clearTimeout(suggestTimeoutRef.current)
    if (val.trim().length >= 2) {
      suggestTimeoutRef.current = setTimeout(() => fetchSuggestions(val.trim()), 300)
    } else {
      setShowSuggestions(false)
    }
  }

  const handleSuggestionPick = (suggestion: string) => {
    setInput(suggestion)
    setShowSuggestions(false)
  }

  const handleSuggestionKeyDown = (e: React.KeyboardEvent) => {
    if (!showSuggestions || suggestions.length === 0) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        handleSend()
      }
      return
    }
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setSelectedSuggestion((p) => Math.min(p + 1, suggestions.length - 1))
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setSelectedSuggestion((p) => Math.max(p - 1, 0))
    } else if (e.key === "Enter" && selectedSuggestion >= 0) {
      e.preventDefault()
      handleSuggestionPick(suggestions[selectedSuggestion])
    } else if (e.key === "Escape") {
      setShowSuggestions(false)
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSend = async () => {
    if (!input.trim() || isSending) return
    setIsSending(true)
    try {
      const tempMsg: ConversationMessageResponse = {
        id: -Date.now(),
        conversation_id: conversationId,
        role: "user",
        content: input,
        created_at: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, tempMsg])
      setInput("")
      setShowSuggestions(false)

      const result = await api.sendMessage(conversationId, { content: input })
      setMessages((prev) => [...prev, result])
      scrollToBottom()
    } catch (err: unknown) {
      const error = err as { detail?: string }
      toast({ title: "Error", description: error.detail || "Failed to send message", variant: "destructive" })
    } finally {
      setIsSending(false)
    }
  }

  const handleTitleSave = async () => {
    setEditingTitle(false)
    if (!newTitle.trim()) return
    try {
      await api.updateConversation(conversationId, { title: newTitle.trim() })
      setTitle(newTitle.trim())
      toast({ title: "Conversation renamed", variant: "success" })
    } catch {
      toast({ title: "Error", description: "Failed to rename conversation", variant: "destructive" })
    }
  }

  const handleDelete = async () => {
    try {
      await api.deleteConversation(conversationId)
      toast({ title: "Conversation deleted", variant: "success" })
      router.push("/dashboard/conversations")
    } catch (err: unknown) {
      const error = err as { detail?: string }
      toast({ title: "Error", description: error.detail || "Failed to delete", variant: "destructive" })
    }
  }

  const handleSetDatabase = async (dbId: string) => {
    setSelectingDb(true)
    try {
      await api.updateConversation(conversationId, { database_id: Number(dbId) })
      setDatabaseId(Number(dbId))
      toast({ title: "Database set", description: "This conversation will now use the selected database.", variant: "success" })
    } catch {
      toast({ title: "Error", description: "Failed to set database", variant: "destructive" })
    } finally {
      setSelectingDb(false)
    }
  }

  const handleSaveTemplate = async () => {
    if (!savingMsg || !templateTitle.trim()) return
    setSaving(true)
    try {
      const sql = extractSql(savingMsg.content) || undefined
      await api.createTemplate({
        title: templateTitle.trim(),
        description: templateDesc.trim() || undefined,
        natural_language: savingMsg.content.slice(0, 500),
        generated_sql: sql,
        database_id: databaseId ?? undefined,
      })
      toast({ title: "Template saved", variant: "success" })
      setSavingMsg(null)
      setTemplateTitle("")
      setTemplateDesc("")
    } catch {
      toast({ title: "Error", description: "Failed to save template", variant: "destructive" })
    } finally {
      setSaving(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => router.push("/dashboard/conversations")}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
              {editingTitle ? (
                <div className="flex items-center gap-2">
                  <input
                    className="rounded-md border bg-background px-2 py-1 text-lg font-semibold"
                    value={newTitle}
                    onChange={(e) => setNewTitle(e.target.value)}
                    onBlur={handleTitleSave}
                    onKeyDown={(e) => { if (e.key === "Enter") { handleTitleSave() } }}
                    autoFocus
                  />
                </div>
              ) : (
                <h1
                  className="text-lg font-semibold cursor-pointer hover:text-primary"
                  onClick={() => { setNewTitle(title); setEditingTitle(true) }}
                >
                  {title}
                </h1>
              )}
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <MessageSquare className="h-3 w-3" />
              {messages.length} messages
              {databaseId && (
                <>
                  <span>·</span>
                  <Database className="h-3 w-3" />
                  {databases.find((d) => d.id === databaseId)?.name || `DB #${databaseId}`}
                </>
              )}
            </div>
          </div>
        </div>
        <Button variant="ghost" size="icon" className="text-destructive" onClick={handleDelete}>
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      {!databaseId && (
        <div className="mx-6 mt-4 flex items-center gap-3 rounded-lg border bg-muted/50 px-4 py-3">
          <Database className="h-5 w-5 text-muted-foreground" />
          <div className="flex-1">
            <p className="text-sm font-medium">Select a database</p>
            <p className="text-xs text-muted-foreground">Choose a database so the AI can understand your schema and generate accurate queries.</p>
          </div>
          <Select onValueChange={handleSetDatabase} disabled={selectingDb}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="Pick a database..." />
            </SelectTrigger>
            <SelectContent>
              {databases.map((db) => (
                <SelectItem key={db.id} value={String(db.id)}>
                  {db.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-muted-foreground">
            <MessageSquare className="mb-4 h-12 w-12" />
            <p className="text-lg font-medium">Start the conversation</p>
            <p className="text-sm">Ask a question about your data</p>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((msg) => (
              <div key={msg.id} className={`flex gap-3 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                {msg.role !== "user" && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
                    <Sparkles className="h-4 w-4 text-primary" />
                  </div>
                )}
                <div className={`max-w-[80%] space-y-1 ${msg.role === "user" ? "order-first" : ""}`}>
                  <div
                    className={`rounded-lg px-4 py-3 text-sm ${
                      msg.role === "user"
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted"
                    }`}
                  >
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                    {msg.role === "assistant" && msg.results && (
                      <div className="mt-3 border-t pt-3">
                        <VisualizationRenderer results={msg.results} />
                        <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                          <span>{msg.results.row_count} rows returned</span>
                          {msg.results.execution_time_ms != null && (
                            <span>· {msg.results.execution_time_ms}ms execution time</span>
                          )}
                        </div>
                      </div>
                    )}
                    {msg.role === "assistant" && msg.error_message && (
                      <div className="mt-2 flex items-start gap-2 rounded bg-destructive/10 p-2 text-xs text-destructive">
                        <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                        <span>{msg.error_message}</span>
                      </div>
                    )}
                    {msg.role === "assistant" && extractSql(msg.content) && (
                      <div className="mt-2 flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 text-xs text-muted-foreground hover:text-primary"
                          onClick={() => { setSavingMsg(msg); setTemplateTitle(""); setTemplateDesc("") }}
                        >
                          <BookOpen className="mr-1 h-3 w-3" />
                          Save as template
                        </Button>
                      </div>
                    )}
                  </div>
                  <div className={`flex items-center gap-2 px-1 ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                    <span className="text-xs text-muted-foreground">{formatDate(msg.created_at)}</span>
                    {msg.tokens_used != null && (
                      <Badge variant="outline" className="text-[10px] px-1 py-0">
                        {msg.tokens_used} tokens
                      </Badge>
                    )}
                    {msg.model_used && (
                      <Badge variant="secondary" className="text-[10px] px-1 py-0">
                        {msg.model_used}
                      </Badge>
                    )}
                  </div>
                </div>
                {msg.role === "user" && (
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary">
                    <User className="h-4 w-4 text-primary-foreground" />
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <div className="border-t px-6 py-4">
        <div className="relative flex gap-3">
          <div className="flex-1 relative">
            <Textarea
              value={input}
              onChange={handleInputChange}
              onKeyDown={handleSuggestionKeyDown}
              placeholder="Ask a follow-up question..."
              rows={2}
              className="resize-none min-h-[2.5rem]"
            />
            {showSuggestions && suggestions.length > 0 && (
              <div
                ref={suggestRef}
                className="absolute bottom-full left-0 right-0 mb-1 rounded-lg border bg-popover shadow-lg"
              >
                {suggestions.map((s, i) => (
                  <button
                    key={i}
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-accent ${
                      i === selectedSuggestion ? "bg-accent" : ""
                    }`}
                    onMouseDown={() => handleSuggestionPick(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
          <Button
            className="shrink-0 self-end"
            size="icon"
            onClick={handleSend}
            disabled={isSending || !input.trim()}
          >
            {isSending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">Press Ctrl+Enter to send</p>
      </div>

      <Dialog open={savingMsg !== null} onOpenChange={(o) => { if (!o) setSavingMsg(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Save as Template</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="tmpl-title">Title</Label>
              <Input
                id="tmpl-title"
                value={templateTitle}
                onChange={(e) => setTemplateTitle(e.target.value)}
                placeholder="Give your template a name..."
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="tmpl-desc">Description (optional)</Label>
              <Input
                id="tmpl-desc"
                value={templateDesc}
                onChange={(e) => setTemplateDesc(e.target.value)}
                placeholder="What does this query do?"
              />
            </div>
            {savingMsg && extractSql(savingMsg.content) && (
              <div className="rounded bg-muted p-2">
                <code className="text-xs line-clamp-3">{extractSql(savingMsg.content)}</code>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSavingMsg(null)}>Cancel</Button>
            <Button onClick={handleSaveTemplate} disabled={saving || !templateTitle.trim()}>
              {saving ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Saving...</> : "Save Template"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
