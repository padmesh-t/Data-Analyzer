"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api-client"
import { useToast } from "@/components/ui/use-toast"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { DataTable, type Column } from "@/components/ui/data-table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Label } from "@/components/ui/label"
import { formatDate } from "@/lib/utils"
import type { ConversationResponse, DatabaseConnectionResponse } from "@/types/api"
import {
  MessageSquare,
  Plus,
  Trash2,
  Loader2,
  Database,
} from "lucide-react"

export default function ConversationsPage() {
  const router = useRouter()
  const { toast } = useToast()
  const [conversations, setConversations] = useState<ConversationResponse[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [isLoading, setIsLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [databases, setDatabases] = useState<DatabaseConnectionResponse[]>([])
  const [selectedDb, setSelectedDb] = useState<string>("")
  const [dialogOpen, setDialogOpen] = useState(false)

  const perPage = 20

  const fetchConversations = async () => {
    setIsLoading(true)
    try {
      const data = await api.listConversations({ page, per_page: perPage })
      setConversations(data.conversations)
      setTotal(data.total)
    } catch (err: unknown) {
      const error = err as { detail?: string }
      toast({ title: "Error", description: error.detail || "Failed to load conversations", variant: "destructive" })
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => { fetchConversations() }, [page])

  const openCreateDialog = async () => {
    try {
      const data = await api.listDatabases({ per_page: 100 })
      setDatabases(data.connections || [])
    } catch {
      setDatabases([])
    }
    setSelectedDb("")
    setDialogOpen(true)
  }

  const handleCreate = async () => {
    setCreating(true)
    try {
      const dbId = selectedDb ? Number(selectedDb) : undefined
      const conv = await api.createConversation(
        dbId ? { database_id: dbId } : undefined,
      )
      toast({ title: "Conversation created", variant: "success" })
      setDialogOpen(false)
      router.push(`/dashboard/conversations/${conv.id}`)
    } catch (err: unknown) {
      const error = err as { detail?: string }
      toast({ title: "Error", description: error.detail || "Failed to create conversation", variant: "destructive" })
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await api.deleteConversation(id)
      toast({ title: "Conversation deleted", variant: "success" })
      fetchConversations()
    } catch (err: unknown) {
      const error = err as { detail?: string }
      toast({ title: "Error", description: error.detail || "Failed to delete", variant: "destructive" })
    }
  }

  const columns: Column<ConversationResponse>[] = [
    {
      key: "title",
      header: "Conversation",
      cell: (c) => (
        <button
          onClick={() => router.push(`/dashboard/conversations/${c.id}`)}
          className="flex items-center gap-3 hover:underline"
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
            <MessageSquare className="h-4 w-4 text-primary" />
          </div>
          <div className="text-left">
            <p className="font-medium">{c.title || "Untitled"}</p>
            <p className="text-xs text-muted-foreground">{c.message_count} messages</p>
          </div>
        </button>
      ),
    },
    {
      key: "database_id",
      header: "Database",
      cell: (c) => c.database_id ? (
        <Badge variant="outline" className="text-xs">
          <Database className="mr-1 h-3 w-3" />
          DB #{c.database_id}
        </Badge>
      ) : (
        <Badge variant="secondary" className="text-xs">No DB</Badge>
      ),
    },
    {
      key: "message_count",
      header: "Messages",
      cell: (c) => <span className="text-sm">{c.message_count}</span>,
    },
    {
      key: "total_tokens",
      header: "Tokens",
      cell: (c) => <span className="text-sm text-muted-foreground">{c.total_tokens || "—"}</span>,
    },
    {
      key: "updated_at",
      header: "Last Active",
      cell: (c) => <span className="text-sm text-muted-foreground">{formatDate(c.updated_at)}</span>,
    },
    {
      key: "actions",
      header: "",
      cell: (c) => (
        <Button
          variant="ghost"
          size="icon"
          className="text-destructive"
          title="Delete"
          onClick={(e) => { e.stopPropagation(); handleDelete(c.id) }}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      ),
    },
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Conversations</h1>
          <p className="text-muted-foreground">Chat with your data using AI</p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button onClick={openCreateDialog}>
              <Plus className="mr-2 h-4 w-4" /> New Conversation
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>New Conversation</DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-2">
              <div className="space-y-2">
                <Label htmlFor="database">Database (optional)</Label>
                <Select value={selectedDb} onValueChange={setSelectedDb}>
                  <SelectTrigger id="database">
                    <SelectValue placeholder="Select a database..." />
                  </SelectTrigger>
                  <SelectContent>
                    {databases.map((db) => (
                      <SelectItem key={db.id} value={String(db.id)}>
                        {db.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Selecting a database lets the AI understand your schema when answering questions.
                  You can also set it later.
                </p>
              </div>
              <Button className="w-full" onClick={handleCreate} disabled={creating}>
                {creating ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Creating...</>
                ) : (
                  "Create Conversation"
                )}
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <Card>
        <CardHeader />
        <CardContent>
          <DataTable
            columns={columns}
            data={conversations}
            total={total}
            page={page}
            perPage={perPage}
            onPageChange={setPage}
            isLoading={isLoading}
          />
        </CardContent>
      </Card>
    </div>
  )
}
