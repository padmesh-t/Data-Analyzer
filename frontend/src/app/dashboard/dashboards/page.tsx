"use client"

import { useEffect, useState, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import { api } from "@/lib/api-client"
import { apiCache } from "@/lib/api-cache"
import { useAuthStore } from "@/store/auth-store"
import { useToast } from "@/components/ui/use-toast"
import { Card, CardContent } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { DataTable, type Column } from "@/components/ui/data-table"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { formatDate } from "@/lib/utils"
import type { DashboardResponse } from "@/types/api"
import type { ConnectionType } from "@/types/api"
import {
  LayoutDashboard, Plus, Trash2, Loader2, Zap, Sparkles, Wand2, Database, PieChart, BarChart3, Table2,
} from "lucide-react"

export default function DashboardsPage() {
  const router = useRouter()
  const { toast } = useToast()
  const { user } = useAuthStore()

  const canCreateDashboard = useMemo(() => {
    if (!user) return false
    const roleNames = user.roles?.map((r) => r.name) || []
    if (roleNames.some((n) => n === "SuperAdmin" || n === "Admin" || n === "Analyst")) return true
    const allPermissions = new Set(user.roles?.flatMap((r) => r.permissions?.map((p) => p.name) || []) || [])
    return allPermissions.has("dashboard.create") || allPermissions.has("access.manage")
  }, [user])

  const canDeleteDashboard = useMemo(() => {
    if (!user) return false
    const roleNames = user.roles?.map((r) => r.name) || []
    if (roleNames.some((n) => n === "SuperAdmin" || n === "Admin" || n === "Analyst")) return true
    const allPermissions = new Set(user.roles?.flatMap((r) => r.permissions?.map((p) => p.name) || []) || [])
    return allPermissions.has("dashboard.delete") || allPermissions.has("access.manage")
  }, [user])

  const [dashboards, setDashboards] = useState<DashboardResponse[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const perPage = 20
  const [isLoading, setIsLoading] = useState(true)
  const [deleteId, setDeleteId] = useState<number | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [autoGenOpen, setAutoGenOpen] = useState(false)

  const fetchDashboards = useCallback(async (forceFresh = false) => {
    const cacheKey = `dashboards:page=${page}`
    try {
      const { data } = await apiCache.swr(
        cacheKey,
        () => api.listDashboards({ page, per_page: perPage }),
        {
          ttlMs: 45000,
          forceFresh,
          onRevalidate: (fresh) => {
            setDashboards(fresh.dashboards)
            setTotal(fresh.total)
          },
        }
      )
      setDashboards(data.dashboards)
      setTotal(data.total)
      setIsLoading(false)
    } catch {
      toast({ title: "Error", description: "Failed to load dashboards", variant: "destructive" })
      setIsLoading(false)
    }
  }, [page, toast])

  useEffect(() => { fetchDashboards() }, [fetchDashboards])

  const handleDelete = async () => {
    if (!deleteId) return
    try {
      await api.deleteDashboard(deleteId)
      apiCache.invalidate("dashboards")
      toast({ title: "Dashboard deleted", variant: "success" })
      setDeleteId(null)
      fetchDashboards(true)
    } catch {
      toast({ title: "Error", description: "Failed to delete dashboard", variant: "destructive" })
    }
  }

  const columns: Column<DashboardResponse>[] = [
    {
      key: "title",
      header: "Dashboard",
      cell: (d) => (
        <button
          onClick={() => router.push(`/dashboard/dashboards/${d.id}`)}
          className="flex items-center gap-3 hover:underline"
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
            <LayoutDashboard className="h-4 w-4 text-primary" />
          </div>
          <div className="text-left">
            <p className="font-medium">{d.title}</p>
            {d.description && (
              <p className="text-xs text-muted-foreground line-clamp-1">{d.description}</p>
            )}
          </div>
        </button>
      ),
    },
    {
      key: "widget_count",
      header: "Widgets",
      cell: (d) => <span className="text-muted-foreground">{d.widget_count}</span>,
    },
    {
      key: "is_template",
      header: "Type",
      cell: (d) => d.is_template
        ? <Badge variant="secondary">Template</Badge>
        : d.auto_generated
          ? <Badge variant="outline" className="gap-1"><Sparkles className="h-3 w-3" />Auto</Badge>
          : <Badge variant="outline">Custom</Badge>,
    },
    {
      key: "updated_at",
      header: "Updated",
      cell: (d) => <span className="text-muted-foreground text-sm">{formatDate(d.updated_at)}</span>,
    },
    ...(canDeleteDashboard ? [{
      key: "actions" as const,
      header: "",
      cell: (d: DashboardResponse) => (
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            onClick={(e) => { e.stopPropagation(); setDeleteId(d.id) }}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ),
    }] : []),
  ]

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Dashboards</h1>
          <p className="text-muted-foreground">{total} dashboard{total !== 1 ? "s" : ""}</p>
        </div>
        <div className="flex items-center gap-2">
          {canCreateDashboard && (
            <>
              <Button variant="outline" onClick={() => setAutoGenOpen(true)}>
                <Wand2 className="mr-2 h-4 w-4" /> Auto-Generate
              </Button>
              <Button onClick={() => setDialogOpen(true)}>
                <Plus className="mr-2 h-4 w-4" /> New Dashboard
              </Button>
            </>
          )}
        </div>
      </div>

      <Card>
        <CardContent>
          <DataTable
            columns={columns}
            data={dashboards}
            total={total}
            page={page}
            perPage={perPage}
            onPageChange={setPage}
            isLoading={isLoading}
          />
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New Dashboard</DialogTitle>
          </DialogHeader>
          <CreateDashboardForm
            onSuccess={(id) => {
              setDialogOpen(false)
              router.push(`/dashboard/dashboards/${id}`)
            }}
            onCancel={() => setDialogOpen(false)}
          />
        </DialogContent>
      </Dialog>

      <AutoGenerateDialog
        open={autoGenOpen}
        onOpenChange={setAutoGenOpen}
        onSuccess={(id) => router.push(`/dashboard/dashboards/${id}`)}
      />

      <Dialog open={!!deleteId} onOpenChange={(o) => { if (!o) setDeleteId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Dashboard</DialogTitle>
          </DialogHeader>
          <p className="text-muted-foreground">Are you sure you want to delete this dashboard? This action cannot be undone.</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteId(null)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDelete}><Trash2 className="mr-2 h-4 w-4" /> Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function AutoGenerateDialog({
  open, onOpenChange, onSuccess,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onSuccess: (id: number) => void
}) {
  const { toast } = useToast()
  const [databases, setDatabases] = useState<Array<{ id: number; name: string }>>([])
  const [dbId, setDbId] = useState<number | "">("")
  const [queryText, setQueryText] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [loadingDb, setLoadingDb] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoadingDb(true)
    api.listDatabases({ per_page: 100 })
      .then((data) => {
        setDatabases(data.connections)
        if (data.connections.length > 0 && !dbId) {
          setDbId(data.connections[0].id)
        }
      })
      .catch(() => setDatabases([]))
      .finally(() => setLoadingDb(false))
  }, [open, dbId])

  const handleGenerate = async () => {
    if (!dbId) return
    setSubmitting(true)
    try {
      const dash = await api.autoGenerateDashboard({
        database_id: Number(dbId),
        query_text: queryText.trim() || undefined,
      })
      apiCache.invalidate("dashboards")
      toast({
        title: "Dashboard generated",
        description: queryText.trim()
          ? `AI generated charts and tables focused strictly on '${queryText.trim()}'.`
          : "AI analyzed the entire database schema and generated charts & tables.",
        variant: "success",
      })
      onOpenChange(false)
      onSuccess(dash.id)
    } catch {
      toast({ title: "Error", description: "Failed to auto-generate dashboard", variant: "destructive" })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <Sparkles className="h-4 w-4" />
            </div>
            <DialogTitle>AI Auto-Generate Dashboard</DialogTitle>
          </div>
        </DialogHeader>
        <div className="space-y-4 py-2">
          <p className="text-sm text-muted-foreground">
            Select a database. AI will analyze the database and construct a tailored dashboard with pie charts, bar charts, and data tables.
          </p>

          <div className="space-y-2">
            <Label className="flex items-center gap-1.5 text-sm font-medium">
              <Database className="h-4 w-4 text-muted-foreground" />
              Database
            </Label>
            <Select
              value={dbId ? String(dbId) : ""}
              onValueChange={(v) => setDbId(Number(v))}
            >
              <SelectTrigger>
                <SelectValue placeholder={loadingDb ? "Loading databases..." : "Select database connection"} />
              </SelectTrigger>
              <SelectContent>
                {databases.map((db) => (
                  <SelectItem key={db.id} value={String(db.id)}>{db.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="auto-query" className="text-sm font-medium">
                Focus Topic / Metric <span className="text-muted-foreground font-normal">(Optional)</span>
              </Label>
            </div>
            <Input
              id="auto-query"
              value={queryText}
              onChange={(e) => setQueryText(e.target.value)}
              placeholder="e.g., salary, revenue, sales, performance, orders"
            />
            <p className="text-xs text-muted-foreground leading-relaxed">
              {queryText.trim()
                ? `AI will strictly generate charts and tables relevant to "${queryText.trim()}".`
                : "Leave empty to automatically analyze the whole database schema and generate relevant data."}
            </p>
          </div>

          <div className="rounded-lg border bg-muted/40 p-3.5 space-y-2.5">
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              What will be generated
            </p>
            <div className="grid grid-cols-3 gap-2">
              <div className="flex flex-col items-center justify-center rounded-md border bg-background p-2.5 text-center shadow-xs">
                <PieChart className="h-5 w-5 text-emerald-500 mb-1" />
                <span className="text-xs font-medium">Pie Chart</span>
                <span className="text-[10px] text-muted-foreground leading-tight mt-0.5">
                  {queryText.trim() ? `${queryText.trim()} Share` : "Distributions"}
                </span>
              </div>
              <div className="flex flex-col items-center justify-center rounded-md border bg-background p-2.5 text-center shadow-xs">
                <BarChart3 className="h-5 w-5 text-blue-500 mb-1" />
                <span className="text-xs font-medium">Bar Chart</span>
                <span className="text-[10px] text-muted-foreground leading-tight mt-0.5">
                  {queryText.trim() ? `${queryText.trim()} Rankings` : "Metrics & Ranks"}
                </span>
              </div>
              <div className="flex flex-col items-center justify-center rounded-md border bg-background p-2.5 text-center shadow-xs">
                <Table2 className="h-5 w-5 text-purple-500 mb-1" />
                <span className="text-xs font-medium">Data Table</span>
                <span className="text-[10px] text-muted-foreground leading-tight mt-0.5">
                  {queryText.trim() ? `Top ${queryText.trim()}` : "Top Records"}
                </span>
              </div>
            </div>
          </div>
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleGenerate} disabled={submitting || !dbId} className="gap-2">
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {queryText.trim() ? `Analyzing ${queryText.trim()}...` : "Analyzing Database..."}
              </>
            ) : (
              <>
                <Wand2 className="h-4 w-4" />
                Generate Dashboard
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CreateDashboardForm({
  onSuccess,
  onCancel,
}: {
  onSuccess: (id: number) => void
  onCancel: () => void
}) {
  const { toast } = useToast()
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim()) return
    setSubmitting(true)
    try {
      const dash = await api.createDashboard({ title: title.trim(), description: description.trim() || undefined })
      toast({ title: "Dashboard created", variant: "success" })
      onSuccess(dash.id)
    } catch {
      toast({ title: "Error", description: "Failed to create dashboard", variant: "destructive" })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="title">Title</Label>
        <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="My Dashboard" required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="desc">Description (optional)</Label>
        <Textarea id="desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Brief description..." />
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
        <Button type="submit" disabled={submitting || !title.trim()}>
          {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
          Create
        </Button>
      </DialogFooter>
    </form>
  )
}
