"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useParams, useRouter } from "next/navigation"
import GridLayout, { type Layout, verticalCompactor } from "react-grid-layout"
import "react-grid-layout/css/styles.css"
import { api } from "@/lib/api-client"
import { useToast } from "@/components/ui/use-toast"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { VisualizationRenderer } from "@/components/visualization/visualization-renderer"
import { useDashboardWs, type LiveWidgetData } from "@/hooks/use-dashboard-ws"
import { formatDate } from "@/lib/utils"
import type {
  WidgetResponse, DashboardDetailResponse, QueryResponse,
  VisualizationSuggestion, QueryResult,
} from "@/types/api"
import {
  AlertCircle, ArrowLeft, Download, Plus, Trash2, Loader2, Settings, GripVertical, Pencil,
  BarChart3, PieChart, LineChart, AreaChart, Table2, LayoutDashboard,
  RefreshCw, Wifi, WifiOff,
} from "lucide-react"

const WIDGET_TYPES = [
  { value: "kpi", label: "KPI", icon: LayoutDashboard },
  { value: "bar_chart", label: "Bar Chart", icon: BarChart3 },
  { value: "pie_chart", label: "Pie Chart", icon: PieChart },
  { value: "line_chart", label: "Line Chart", icon: LineChart },
  { value: "area_chart", label: "Area Chart", icon: AreaChart },
  { value: "table", label: "Table", icon: Table2 },
] as const

export default function DashboardDetailPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const { toast } = useToast()
  const dashboardId = Number(params.id)

  const [dash, setDash] = useState<DashboardDetailResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState("")
  const [description, setDescription] = useState("")
  const [isPublic, setIsPublic] = useState(false)
  const [saving, setSaving] = useState(false)
  const [addWidgetOpen, setAddWidgetOpen] = useState(false)
  const [editingWidget, setEditingWidget] = useState<WidgetResponse | null>(null)
  const [editWidgetOpen, setEditWidgetOpen] = useState(false)
  const [deleteWidgetId, setDeleteWidgetId] = useState<number | null>(null)
  const [layoutUpdating, setLayoutUpdating] = useState(false)
  const layoutTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const lastSavedLayoutRef = useRef<string>("")

  const { connected, liveData, activePollers, refreshWidget, refreshAll } = useDashboardWs(dashboardId)

  const fetchDashboard = useCallback(async () => {
    setIsLoading(true)
    try {
      const data = await api.getDashboardById(dashboardId)
      setDash(data)
      setTitle(data.title)
      setDescription(data.description || "")
      setIsPublic(data.is_public)
    } catch {
      toast({ title: "Error", description: "Failed to load dashboard", variant: "destructive" })
    } finally {
      setIsLoading(false)
    }
  }, [dashboardId, toast])

  useEffect(() => { fetchDashboard() }, [fetchDashboard])

  const layout = useMemo(() => {
    if (!dash) return []
    return dash.widgets.map((w) => ({
      i: String(w.id),
      x: w.position_x,
      y: w.position_y,
      w: w.width,
      h: w.height,
    }))
  }, [dash])

  const handleLayoutChange = useCallback((newLayout: Layout) => {
    if (!dash) return
    if (layoutTimerRef.current) clearTimeout(layoutTimerRef.current)
    const widgets = newLayout.map((item) => ({
      id: Number(item.i),
      position_x: item.x,
      position_y: item.y,
      width: item.w,
      height: item.h,
    }))
    const serialized = JSON.stringify(widgets)
    if (serialized === lastSavedLayoutRef.current) return

    setDash((prev) => {
      if (!prev) return prev
      const widgetMap = new Map(newLayout.map((l) => [l.i, l]))
      return {
        ...prev,
        widgets: prev.widgets.map((w) => {
          const pos = widgetMap.get(String(w.id))
          return pos ? { ...w, position_x: pos.x, position_y: pos.y, width: pos.w, height: pos.h } : w
        }),
      }
    })

    layoutTimerRef.current = setTimeout(async () => {
      setLayoutUpdating(true)
      try {
        const updated = await api.updateDashboardLayout(dashboardId, { widgets })
        lastSavedLayoutRef.current = serialized
        setDash((prev) => prev ? { ...prev, widgets: updated } : prev)
      } catch {
        toast({ title: "Error", description: "Failed to save layout", variant: "destructive" })
      } finally {
        setLayoutUpdating(false)
      }
    }, 500)
  }, [dash, dashboardId, toast])

  const handleSaveDetails = async () => {
    if (!title.trim()) return
    setSaving(true)
    try {
      const updated = await api.updateDashboard(dashboardId, {
        title: title.trim(),
        description: description.trim() || undefined,
        is_public: isPublic,
      })
      setDash(updated)
      setEditing(false)
      toast({ title: "Dashboard updated", variant: "success" })
    } catch {
      toast({ title: "Error", description: "Failed to update dashboard", variant: "destructive" })
    } finally {
      setSaving(false)
    }
  }

  const handleAddWidget = async (widgetType: string, widgetTitle: string, queryId?: number) => {
    const maxY = dash ? Math.max(0, ...dash.widgets.map((w) => w.position_y + w.height)) : 0
    try {
      const widget = await api.addWidget(dashboardId, {
        widget_type: widgetType,
        title: widgetTitle,
        position_x: 0,
        position_y: maxY,
        width: 6,
        height: 4,
        query_id: queryId,
      })
      setDash((prev) => prev ? { ...prev, widgets: [...prev.widgets, widget] } : prev)
      setAddWidgetOpen(false)
      toast({ title: "Widget added", variant: "success" })
    } catch {
      toast({ title: "Error", description: "Failed to add widget", variant: "destructive" })
    }
  }

  const handleUpdateWidget = async (widgetId: number, data: {
    title: string
    widget_type: string
    query_id?: number | null
    config?: Record<string, unknown>
    refresh_interval?: number
  }) => {
    try {
      const updated = await api.updateWidget(dashboardId, widgetId, {
        widget_type: data.widget_type,
        title: data.title,
        position_x: 0,
        position_y: 0,
        width: 6,
        height: 4,
        query_id: data.query_id ?? undefined,
        config: { ...(data.config || {}), refresh_interval: data.refresh_interval ?? 0 },
      })
      setDash((prev) => prev ? {
        ...prev,
        widgets: prev.widgets.map((w) => w.id === widgetId ? updated : w),
      } : prev)
      setEditWidgetOpen(false)
      setEditingWidget(null)
      toast({ title: "Widget updated", variant: "success" })
      if (data.refresh_interval && data.refresh_interval > 0) {
        refreshWidget(widgetId)
      }
    } catch {
      toast({ title: "Error", description: "Failed to update widget", variant: "destructive" })
    }
  }

  const handleDeleteWidget = async () => {
    if (!deleteWidgetId) return
    try {
      await api.deleteWidget(dashboardId, deleteWidgetId)
      setDash((prev) => prev ? { ...prev, widgets: prev.widgets.filter((w) => w.id !== deleteWidgetId) } : prev)
      setDeleteWidgetId(null)
      toast({ title: "Widget deleted", variant: "success" })
    } catch {
      toast({ title: "Error", description: "Failed to delete widget", variant: "destructive" })
    }
  }

  const exportWidgetCSV = useCallback(async (widget: WidgetResponse) => {
    const ld = liveData.get(widget.id)
    let results: QueryResult | null | undefined = ld?.results
    if (!results && widget.query_id) {
      try {
        const q = await api.getQueryById(widget.query_id)
        results = q.results
      } catch {
        toast({ title: "Error", description: "No data to export", variant: "destructive" })
        return
      }
    }
    if (!results?.columns || !results?.rows || results.rows.length === 0) {
      toast({ title: "Error", description: "No data to export", variant: "destructive" })
      return
    }
    const rows = results.rows.map((row) => {
      const obj: Record<string, unknown> = {}
      results.columns.forEach((col, i) => { obj[col] = row[i] })
      return obj
    })
    downloadCSV(results.columns, rows, widget.title)
  }, [liveData, toast])

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-96" />
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-48 rounded-lg" />
          ))}
        </div>
      </div>
    )
  }

  if (!dash) {
    return (
      <div className="flex flex-col items-center justify-center py-20">
        <LayoutDashboard className="h-12 w-12 text-muted-foreground mb-4" />
        <h2 className="text-xl font-semibold">Dashboard not found</h2>
        <Button variant="outline" className="mt-4" onClick={() => router.push("/dashboard/dashboards")}>
          <ArrowLeft className="mr-2 h-4 w-4" /> Back
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div className="flex-1">
          {editing ? (
            <div className="space-y-3 max-w-lg">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Dashboard title" />
              <Textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Description..." rows={2} />
              <div className="flex items-center gap-2">
                <Switch id="is-public" checked={isPublic} onCheckedChange={setIsPublic} />
                <Label htmlFor="is-public" className="text-sm">Public sharing</Label>
              </div>
              <div className="flex gap-2">
                <Button size="sm" onClick={handleSaveDetails} disabled={saving || !title.trim()}>
                  {saving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                  Save
                </Button>
                <Button size="sm" variant="outline" onClick={() => { setEditing(false); setTitle(dash.title); setDescription(dash.description || "") }}>
                  Cancel
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-3">
                <h1 className="text-2xl font-bold">{dash.title}</h1>
                {dash.auto_generated && <Badge variant="outline" className="gap-1">Auto-generated</Badge>}
                <ConnectionBadge connected={connected} pollerCount={activePollers.length} />
              </div>
              {dash.description && <p className="text-muted-foreground mt-1">{dash.description}</p>}
              <div className="flex items-center gap-4 mt-2 text-xs text-muted-foreground">
                <span>{dash.widgets.length} widget{dash.widgets.length !== 1 ? "s" : ""}</span>
                <span>Updated {formatDate(dash.updated_at)}</span>
                {activePollers.length > 0 && <span>{activePollers.length} auto-refresh active</span>}
              </div>
            </>
          )}
        </div>
        <div className="flex items-center gap-2">
          {activePollers.length > 0 && (
            <Button variant="outline" size="sm" onClick={() => refreshAll()}>
              <RefreshCw className="mr-1 h-4 w-4" /> Refresh All
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            <Settings className="mr-1 h-4 w-4" /> Edit
          </Button>
          <Button size="sm" onClick={() => setAddWidgetOpen(true)}>
            <Plus className="mr-1 h-4 w-4" /> Add Widget
          </Button>
        </div>
      </div>

      {dash.widgets.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-20">
            <LayoutDashboard className="h-12 w-12 text-muted-foreground mb-4" />
            <p className="text-lg font-medium">No widgets yet</p>
            <p className="text-muted-foreground mb-4">Add a widget to start building your dashboard</p>
            <Button onClick={() => setAddWidgetOpen(true)}>
              <Plus className="mr-2 h-4 w-4" /> Add Widget
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="relative">
          {layoutUpdating && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/50 rounded-lg">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          )}
          <GridLayout
            className="layout"
            layout={layout}
            width={1200}
            gridConfig={{ cols: 12, rowHeight: 80 }}
            dragConfig={{ handle: ".drag-handle", enabled: true }}
            resizeConfig={{ enabled: true }}
            compactor={verticalCompactor}
            onLayoutChange={handleLayoutChange}
          >
            {dash.widgets.map((widget) => (
              <Card key={widget.id} className="overflow-hidden">
                <CardHeader className="flex flex-row items-center justify-between py-2 px-4">
                  <div className="flex items-center gap-2">
                    <div className="drag-handle cursor-grab active:cursor-grabbing">
                      <GripVertical className="h-4 w-4 text-muted-foreground" />
                    </div>
                    <CardTitle className="text-sm font-medium">{widget.title}</CardTitle>
                    <WidgetTypeBadge type={widget.widget_type} />
                    {activePollers.includes(widget.id) && connected && (
                      <div className="h-2 w-2 rounded-full bg-green-500" title="Auto-refreshing" />
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => exportWidgetCSV(widget)}
                      title="Export CSV"
                    >
                      <Download className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => refreshWidget(widget.id)}
                      title="Refresh now"
                    >
                      <RefreshCw className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => { setEditingWidget(widget); setEditWidgetOpen(true) }}
                    >
                      <Pencil className="h-3 w-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => setDeleteWidgetId(widget.id)}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="p-4 pt-0" style={{ height: `calc(100% - 40px)` }}>
                  <WidgetContent widget={widget} liveData={liveData.get(widget.id)} />
                </CardContent>
              </Card>
            ))}
          </GridLayout>
        </div>
      )}

      <AddWidgetDialog
        open={addWidgetOpen}
        onOpenChange={setAddWidgetOpen}
        onAdd={handleAddWidget}
      />

      {editingWidget && (
        <EditWidgetDialog
          widget={editingWidget}
          open={editWidgetOpen}
          onOpenChange={(o) => { if (!o) { setEditWidgetOpen(false); setEditingWidget(null) } }}
          onSave={handleUpdateWidget}
        />
      )}

      <Dialog open={!!deleteWidgetId} onOpenChange={(o) => { if (!o) setDeleteWidgetId(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Widget</DialogTitle>
          </DialogHeader>
          <p className="text-muted-foreground">Are you sure you want to delete this widget?</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteWidgetId(null)}>Cancel</Button>
            <Button variant="destructive" onClick={handleDeleteWidget}><Trash2 className="mr-2 h-4 w-4" /> Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function downloadCSV(columns: string[], rows: Record<string, unknown>[], filename: string) {
  const header = columns.map((c) => `"${c.replace(/"/g, '""')}"`).join(",")
  const body = rows.map((row) => columns.map((c) => {
    const val = row[c]
    if (val == null) return ""
    return `"${String(val).replace(/"/g, '""')}"`
  }).join(",")).join("\n")
  const blob = new Blob([`${header}\n${body}`], { type: "text/csv;charset=utf-8;" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function ConnectionBadge({ connected, pollerCount }: { connected: boolean; pollerCount: number }) {
  if (connected && pollerCount > 0) {
    return (
      <Badge variant="success" className="gap-1 text-xs">
        <Wifi className="h-3 w-3" /> Live
      </Badge>
    )
  }
  if (!connected) {
    return (
      <Badge variant="outline" className="gap-1 text-xs text-muted-foreground">
        <WifiOff className="h-3 w-3" /> Offline
      </Badge>
    )
  }
  return null
}

function WidgetTypeBadge({ type }: { type: string }) {
  const info = WIDGET_TYPES.find((t) => t.value === type)
  if (!info) return <Badge variant="secondary">{type}</Badge>
  const Icon = info.icon
  return (
    <Badge variant="secondary" className="gap-1">
      <Icon className="h-3 w-3" /> {info.label}
    </Badge>
  )
}

function WidgetPlaceholder({ widget }: { widget: WidgetResponse }) {
  const info = WIDGET_TYPES.find((t) => t.value === widget.widget_type)
  const Icon = info?.icon || LayoutDashboard
  return (
    <div className="flex h-full w-full items-center justify-center text-muted-foreground">
      <div className="text-center">
        <Icon className="h-8 w-8 mx-auto opacity-50" />
        <p className="mt-2 text-sm">{widget.title}</p>
        <p className="text-xs">Connect to a query or configure data source</p>
      </div>
    </div>
  )
}

function WidgetContent({ widget, liveData }: { widget: WidgetResponse; liveData?: LiveWidgetData }) {
  const [query, setQuery] = useState<QueryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!widget.query_id) {
      setQuery(null)
      setLoading(false)
      setError(null)
      return
    }
    if (liveData) return
    setLoading(true)
    setError(null)
    api.getQueryById(widget.query_id)
      .then(setQuery)
      .catch((err: unknown) => {
        if (err instanceof Error) setError(err.message)
        else setError("Failed to load query data")
      })
      .finally(() => setLoading(false))
  }, [widget.query_id, liveData])

  if (!widget.query_id) return <WidgetPlaceholder widget={widget} />

  if (liveData) {
    return <WidgetChartRenderer widget={widget} results={liveData.results} />
  }

  if (loading) return (
    <div className="flex h-full w-full items-center justify-center">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  )
  if (error || !query?.results) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-4">
        <AlertCircle className="h-6 w-6 text-destructive" />
        <p className="text-sm text-destructive font-medium">Query Error</p>
        <p className="text-xs text-muted-foreground text-center max-w-[200px] line-clamp-3">{error || "Query returned no results"}</p>
      </div>
    )
  }

  return <WidgetChartRenderer widget={widget} results={query.results} suggestions={query.suggested_visualizations} />
}

function WidgetChartRenderer({
  widget, results, suggestions,
}: {
  widget: WidgetResponse
  results: QueryResult
  suggestions?: VisualizationSuggestion[]
}) {
  const suggestion = useMemo(() => {
    if (suggestions && suggestions.length > 0) {
      const match = suggestions.find((s) => s.type === widget.widget_type)
      if (match) return match
    }
    return {
      type: widget.widget_type,
      title: widget.title,
      config: (widget.config as Record<string, unknown>) || {},
    } as VisualizationSuggestion
  }, [widget, suggestions])

  if (widget.widget_type === suggestion.type) {
    return <VisualizationRenderer results={results} suggestions={[suggestion]} />
  }

  return <VisualizationRenderer results={results} suggestions={suggestions || [suggestion]} />
}

function QueryPicker({
  value, onChange,
}: {
  value?: number | null
  onChange: (queryId: number | null) => void
}) {
  const [queries, setQueries] = useState<Array<{ id: number; natural_language: string; generated_sql?: string }>>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setLoading(true)
    api.listQueries({ per_page: 50 })
      .then((data) => setQueries(data.queries))
      .catch(() => setQueries([]))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="space-y-2">
      <Label>Linked Query</Label>
      <Select
        value={value ? String(value) : "none"}
        onValueChange={(v) => onChange(v === "none" ? null : Number(v))}
      >
        <SelectTrigger>
          <SelectValue placeholder={loading ? "Loading queries..." : "Select a query"} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="none">No query (placeholder)</SelectItem>
          {queries.map((q) => (
            <SelectItem key={q.id} value={String(q.id)}>
              <span className="line-clamp-1">{q.natural_language || `Query #${q.id}`}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function AddWidgetDialog({
  open, onOpenChange, onAdd,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onAdd: (type: string, title: string, queryId?: number) => void
}) {
  const [widgetType, setWidgetType] = useState("kpi")
  const [widgetTitle, setWidgetTitle] = useState("")
  const [queryId, setQueryId] = useState<number | null>(null)

  const handleAdd = () => {
    if (!widgetTitle.trim()) return
    onAdd(widgetType, widgetTitle.trim(), queryId ?? undefined)
    setWidgetTitle("")
    setWidgetType("kpi")
    setQueryId(null)
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) { setQueryId(null); setWidgetTitle(""); setWidgetType("kpi") }; onOpenChange(o) }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Widget</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Widget Type</Label>
            <Select value={widgetType} onValueChange={setWidgetType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WIDGET_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    <div className="flex items-center gap-2">
                      <t.icon className="h-4 w-4" />
                      {t.label}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="widget-title">Title</Label>
            <Input
              id="widget-title"
              value={widgetTitle}
              onChange={(e) => setWidgetTitle(e.target.value)}
              placeholder="Widget title"
            />
          </div>
          <QueryPicker value={queryId} onChange={setQueryId} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleAdd} disabled={!widgetTitle.trim()}>Add</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function EditWidgetDialog({
  widget, open, onOpenChange, onSave,
}: {
  widget: WidgetResponse
  open: boolean
  onOpenChange: (o: boolean) => void
  onSave: (widgetId: number, data: {
    title: string
    widget_type: string
    query_id?: number | null
    config?: Record<string, unknown>
    refresh_interval?: number
  }) => void
}) {
  const [title, setTitle] = useState(widget.title)
  const [widgetType, setWidgetType] = useState(widget.widget_type)
  const [queryId, setQueryId] = useState<number | null>(widget.query_id ?? null)
  const [selectedQuery, setSelectedQuery] = useState<QueryResponse | null>(null)
  const [loadingQuery, setLoadingQuery] = useState(false)
  const [xCol, setXCol] = useState<string>("")
  const [yCol, setYCol] = useState<string>("")
  const [refreshInterval, setRefreshInterval] = useState<number>(0)

  useEffect(() => {
    setTitle(widget.title)
    setWidgetType(widget.widget_type)
    setQueryId(widget.query_id ?? null)
    setXCol("")
    setYCol("")
    setSelectedQuery(null)
    const cfg = widget.config as Record<string, unknown> | undefined
    setRefreshInterval((cfg?.refresh_interval as number) || 0)
  }, [widget])

  useEffect(() => {
    if (!queryId) {
      setSelectedQuery(null)
      return
    }
    setLoadingQuery(true)
    api.getQueryById(queryId)
      .then((q) => {
        setSelectedQuery(q)
        const cfg = widget.config as Record<string, unknown> | undefined
        if (cfg?.x) setXCol(String(cfg.x))
        if (cfg?.y) setYCol(String(cfg.y))
      })
      .catch(() => setSelectedQuery(null))
      .finally(() => setLoadingQuery(false))
  }, [queryId, widget.config])

  const columns = selectedQuery?.results?.columns || []

  const handleSave = () => {
    if (!title.trim()) return
    const config: Record<string, unknown> = {}
    if (xCol && xCol !== "__auto__") config.x = xCol
    if (yCol && yCol !== "__auto__") config.y = yCol
    if ((widgetType === "pie_chart") && xCol && xCol !== "__auto__") config.label = xCol
    if ((widgetType === "pie_chart") && yCol && yCol !== "__auto__") config.value = yCol
    onSave(widget.id, {
      title: title.trim(),
      widget_type: widgetType,
      query_id: queryId,
      config: Object.keys(config).length > 0 ? config : undefined,
      refresh_interval: refreshInterval,
    })
  }

  const showAxisConfig = selectedQuery?.results && columns.length > 0 && !["kpi", "table"].includes(widgetType)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Edit Widget</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="edit-title">Title</Label>
            <Input id="edit-title" value={title} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label>Widget Type</Label>
            <Select value={widgetType} onValueChange={setWidgetType}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {WIDGET_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>
                    <div className="flex items-center gap-2">
                      <t.icon className="h-4 w-4" />
                      {t.label}
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <QueryPicker value={queryId} onChange={setQueryId} />

          {loadingQuery && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading query...
            </div>
          )}

          {showAxisConfig && (
            <div className="space-y-3 rounded-lg border p-3">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Axis Mapping</p>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-xs">X / Label Column</Label>
                  <Select value={xCol} onValueChange={(v) => setXCol(v === "__auto__" ? "" : v)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Auto" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__auto__">Auto-detect</SelectItem>
                      {columns.map((c) => (
                        <SelectItem key={c} value={c}>{c}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Y / Value Column</Label>
                  <Select value={yCol} onValueChange={(v) => setYCol(v === "__auto__" ? "" : v)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Auto" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__auto__">Auto-detect</SelectItem>
                      {columns.map((c) => (
                        <SelectItem key={c} value={c}>{c}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          )}

          <div className="space-y-2 rounded-lg border p-3">
            <Label htmlFor="refresh-interval">Auto-refresh Interval (seconds)</Label>
            <div className="flex items-center gap-3">
              <Input
                id="refresh-interval"
                type="number"
                min={0}
                max={3600}
                value={refreshInterval}
                onChange={(e) => setRefreshInterval(Math.max(0, parseInt(e.target.value) || 0))}
                className="w-24"
              />
              <span className="text-xs text-muted-foreground">
                {refreshInterval === 0
                  ? "Disabled — load data once"
                  : refreshInterval < 5
                    ? "⚠ Minimum 5s recommended"
                    : `Updates every ${refreshInterval}s via WebSocket`}
              </span>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={!title.trim()}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
