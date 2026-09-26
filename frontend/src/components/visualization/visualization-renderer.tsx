"use client"

import React, { useEffect, useState } from "react"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend,
  LineChart, Line,
  AreaChart, Area,
} from "recharts"
import type { QueryResult, VisualizationSuggestion } from "@/types/api"

const CHART_COLORS = [
  "#3b82f6", // Blue
  "#10b981", // Emerald
  "#f59e0b", // Amber
  "#ef4444", // Rose / Red
  "#8b5cf6", // Purple / Violet
  "#06b6d4", // Cyan
  "#ec4899", // Pink
  "#f97316", // Orange
  "#14b8a6", // Teal
  "#6366f1", // Indigo
  "#84cc16", // Lime
  "#a855f7", // Fuchsia
  "#0ea5e9", // Sky
  "#eab308", // Yellow
]

function getColor(i: number): string {
  return CHART_COLORS[Math.abs(i) % CHART_COLORS.length]
}

function isNumeric(val: unknown): boolean {
  if (typeof val === "number") return !isNaN(val)
  if (typeof val === "string") {
    const cleaned = val.replace(/[$€£,%\s]/g, "").trim()
    const n = Number(cleaned)
    return !isNaN(n) && cleaned !== ""
  }
  return false
}

function parseNumeric(val: unknown): number {
  if (typeof val === "number") return isNaN(val) ? 0 : val
  if (typeof val === "string") {
    const cleaned = val.replace(/[$€£,%\s]/g, "").trim()
    const n = Number(cleaned)
    return isNaN(n) ? 0 : n
  }
  return 0
}

function formatNumber(val: unknown): string {
  if (typeof val === "number") {
    return val.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
  if (typeof val === "string" && isNumeric(val)) {
    return parseNumeric(val).toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
  return String(val ?? "")
}

function pickNumericCol(results: QueryResult): string | null {
  if (!results.columns || results.columns.length === 0) return null
  for (const col of results.columns) {
    const colIdx = results.columns.indexOf(col)
    const nonNullValues = (results.rows || [])
      .map((r) => r[colIdx])
      .filter((v) => v !== null && v !== undefined && v !== "")
    if (nonNullValues.length > 0 && nonNullValues.every((v) => isNumeric(v))) {
      return col
    }
  }
  return null
}

function pickStringCol(results: QueryResult, exclude?: string): string | null {
  if (!results.columns || results.columns.length === 0) return null
  for (const col of results.columns) {
    if (col === exclude) continue
    const colIdx = results.columns.indexOf(col)
    const nonNullValues = (results.rows || [])
      .map((r) => r[colIdx])
      .filter((v) => v !== null && v !== undefined && v !== "")
    if (nonNullValues.length > 0 && !nonNullValues.every((v) => isNumeric(v))) {
      return col
    }
  }
  return null
}

function buildData(results: QueryResult) {
  if (!results.columns || !results.rows) return []

  const numericCols = new Set<string>()
  for (const col of results.columns) {
    const colIdx = results.columns.indexOf(col)
    const nonNullValues = results.rows
      .map((r) => r[colIdx])
      .filter((v) => v !== null && v !== undefined && v !== "")
    if (nonNullValues.length > 0 && nonNullValues.every((v) => isNumeric(v))) {
      numericCols.add(col)
    }
  }

  return results.rows.map((row) => {
    const item: Record<string, unknown> = {}
    results.columns.forEach((col, i) => {
      const val = row[i]
      if (numericCols.has(col)) {
        item[col] = parseNumeric(val)
      } else {
        item[col] = val !== null && val !== undefined ? String(val) : ""
      }
    })
    return item
  })
}

function fixBarConfig(results: QueryResult, config?: Record<string, unknown>) {
  const numCol = pickNumericCol(results)
  const strCol = pickStringCol(results)

  let x = (config?.x as string) || (config?.label as string) || strCol || results.columns[0]
  let y = (config?.y as string) || (config?.value as string) || numCol || results.columns[1] || results.columns[0]

  const rawData = buildData(results)
  const data = rawData.map((item) => {
    if (results.columns.length === 1) {
      return {
        ...item,
        __label__: results.columns[0].replace("_", " ").toUpperCase(),
      }
    }
    return item
  })

  if (results.columns.length === 1) {
    return { data, x: "__label__", y: results.columns[0] }
  }

  if (data.length > 0 && results.columns.length > 0) {
    const xIdx = results.columns.indexOf(x)
    const yIdx = results.columns.indexOf(y)

    const isXNum = xIdx >= 0 && isNumeric(results.rows[0]?.[xIdx])
    const isYNum = yIdx >= 0 && isNumeric(results.rows[0]?.[yIdx])

    if (isXNum && !isYNum) {
      const tmp = x
      x = y
      y = tmp
    } else if (!isYNum && numCol) {
      y = numCol
    }

    if (x === y && results.columns.length >= 2) {
      if (strCol && strCol !== y) {
        x = strCol
      } else if (results.columns[0] === y) {
        x = results.columns[1]
      } else {
        x = results.columns[0]
      }
    }
  }
  return { data, x, y }
}

function fixPieConfig(results: QueryResult, config?: Record<string, unknown>) {
  const numCol = pickNumericCol(results)
  const strCol = pickStringCol(results)

  let label = (config?.label as string) || (config?.x as string) || strCol || results.columns[0]
  let value = (config?.value as string) || (config?.y as string) || numCol || results.columns[1] || results.columns[0]

  const rawData = buildData(results)
  const data = rawData.map((item) => {
    if (results.columns.length === 1) {
      return {
        ...item,
        __label__: results.columns[0].replace("_", " ").toUpperCase(),
      }
    }
    return item
  })

  if (results.columns.length === 1) {
    return { data, label: "__label__", value: results.columns[0] }
  }

  if (data.length > 0 && results.columns.length > 0) {
    const labelIdx = results.columns.indexOf(label)
    const valueIdx = results.columns.indexOf(value)

    const isLabelNum = labelIdx >= 0 && isNumeric(results.rows[0]?.[labelIdx])
    const isValueNum = valueIdx >= 0 && isNumeric(results.rows[0]?.[valueIdx])

    if (isLabelNum && !isValueNum) {
      const tmp = label
      label = value
      value = tmp
    } else if (!isValueNum && numCol) {
      value = numCol
    }

    if (label === value && results.columns.length >= 2) {
      if (strCol && strCol !== value) {
        label = strCol
      } else if (results.columns[0] === value) {
        label = results.columns[1]
      } else {
        label = results.columns[0]
      }
    }
  }
  return { data, label, value }
}

function BarChartView({ results, config }: { results: QueryResult; config?: Record<string, unknown> }) {
  const { data, x, y } = fixBarConfig(results, config)

  if (!data || data.length === 0) {
    return <TableView results={results} />
  }

  return (
    <div className="w-full h-[280px] min-h-[260px] flex flex-col justify-center">
      <ResponsiveContainer width="100%" height={260} minHeight={240}>
        <BarChart data={data} margin={{ top: 10, right: 15, bottom: 30, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" strokeOpacity={0.6} />
          <XAxis
            dataKey={x}
            tick={{ fontSize: 11, fill: "currentColor" }}
            interval={0}
            angle={data.length > 4 ? -20 : 0}
            textAnchor={data.length > 4 ? "end" : "middle"}
            height={40}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(val) => formatNumber(val)}
            width={65}
          />
          <Tooltip
            formatter={(val: unknown) => [formatNumber(val), y || "Value"]}
            contentStyle={{
              backgroundColor: "hsl(var(--popover, #ffffff))",
              borderColor: "hsl(var(--border, #e2e8f0))",
              borderRadius: "0.5rem",
              color: "hsl(var(--popover-foreground, #0f172a))",
              fontSize: "12px",
              boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
            }}
          />
          <Bar dataKey={y} fill={getColor(0)} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function PieChartView({ results, config }: { results: QueryResult; config?: Record<string, unknown> }) {
  const { data, label, value } = fixPieConfig(results, config)

  if (!data || data.length === 0) {
    return <TableView results={results} />
  }

  // Filter out any zero/null entries if others exist
  const validEntries = data.filter((item) => parseNumeric(item[value]) > 0)
  const effectiveData = validEntries.length > 0 ? validEntries : data

  // If there are more than 8 categories, take the top 7 and group the rest into "Other"
  let pieData = effectiveData
  if (effectiveData.length > 8) {
    const sorted = [...effectiveData].sort((a, b) => Number(b[value] || 0) - Number(a[value] || 0))
    const top = sorted.slice(0, 7)
    const rest = sorted.slice(7)
    const restSum = rest.reduce((acc, curr) => acc + Number(curr[value] || 0), 0)
    if (restSum > 0) {
      top.push({
        [label]: "Other",
        [value]: restSum,
      })
    }
    pieData = top
  }

  return (
    <div className="w-full h-full min-h-[250px] flex flex-col items-center justify-center">
      <ResponsiveContainer width="100%" height={260} minHeight={230}>
        <PieChart margin={{ top: 22, right: 22, bottom: 12, left: 22 }}>
          <Pie
            data={pieData}
            dataKey={value}
            nameKey={label}
            cx="50%"
            cy="46%"
            outerRadius={65}
            innerRadius={28}
            paddingAngle={pieData.length > 1 ? 2 : 0}
            label={({ name, percent }: { name?: string; percent?: number }) => {
              const p = percent != null && !isNaN(percent) ? `${(percent * 100).toFixed(0)}%` : ""
              const n = name ? (name.length > 13 ? `${name.slice(0, 11)}…` : name) : ""
              return n && p ? `${n}: ${p}` : n || p
            }}
            labelLine={{ strokeWidth: 1 }}
          >
            {pieData.map((_, i) => (
              <Cell key={`pie-cell-${i}`} fill={getColor(i)} stroke="#ffffff" strokeWidth={1.5} />
            ))}
          </Pie>
          <Tooltip
            formatter={(val: unknown) => [formatNumber(val), (config?.value as string) || value || "Value"]}
            contentStyle={{
              backgroundColor: "hsl(var(--popover, #ffffff))",
              borderColor: "hsl(var(--border, #e2e8f0))",
              borderRadius: "0.5rem",
              color: "hsl(var(--popover-foreground, #0f172a))",
              fontSize: "12px",
              boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
            }}
          />
          <Legend
            verticalAlign="bottom"
            height={36}
            wrapperStyle={{ fontSize: "11px", paddingTop: "4px" }}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  )
}

function LineChartView({ results, config }: { results: QueryResult; config?: Record<string, unknown> }) {
  const { data, x, y } = fixBarConfig(results, config)

  if (!data || data.length === 0) {
    return <TableView results={results} />
  }

  return (
    <div className="w-full h-[280px] min-h-[260px] flex flex-col justify-center">
      <ResponsiveContainer width="100%" height={260} minHeight={240}>
        <LineChart data={data} margin={{ top: 10, right: 15, bottom: 30, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" strokeOpacity={0.6} />
          <XAxis
            dataKey={x}
            tick={{ fontSize: 11, fill: "currentColor" }}
            interval={0}
            angle={data.length > 4 ? -20 : 0}
            textAnchor={data.length > 4 ? "end" : "middle"}
            height={40}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(val) => formatNumber(val)}
            width={65}
          />
          <Tooltip
            formatter={(val: unknown) => [formatNumber(val), y || "Value"]}
            contentStyle={{
              backgroundColor: "hsl(var(--popover, #ffffff))",
              borderColor: "hsl(var(--border, #e2e8f0))",
              borderRadius: "0.5rem",
              color: "hsl(var(--popover-foreground, #0f172a))",
              fontSize: "12px",
              boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
            }}
          />
          <Line
            type="monotone"
            dataKey={y}
            stroke={getColor(0)}
            strokeWidth={2.5}
            dot={{ r: 4, fill: getColor(0) }}
            activeDot={{ r: 6 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function AreaChartView({ results, config }: { results: QueryResult; config?: Record<string, unknown> }) {
  const { data, x, y } = fixBarConfig(results, config)

  if (!data || data.length === 0) {
    return <TableView results={results} />
  }

  return (
    <div className="w-full h-[280px] min-h-[260px] flex flex-col justify-center">
      <ResponsiveContainer width="100%" height={260} minHeight={240}>
        <AreaChart data={data} margin={{ top: 10, right: 15, bottom: 30, left: 10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" strokeOpacity={0.6} />
          <XAxis
            dataKey={x}
            tick={{ fontSize: 11, fill: "currentColor" }}
            interval={0}
            angle={data.length > 4 ? -20 : 0}
            textAnchor={data.length > 4 ? "end" : "middle"}
            height={40}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "currentColor" }}
            tickFormatter={(val) => formatNumber(val)}
            width={65}
          />
          <Tooltip
            formatter={(val: unknown) => [formatNumber(val), y || "Value"]}
            contentStyle={{
              backgroundColor: "hsl(var(--popover, #ffffff))",
              borderColor: "hsl(var(--border, #e2e8f0))",
              borderRadius: "0.5rem",
              color: "hsl(var(--popover-foreground, #0f172a))",
              fontSize: "12px",
              boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1)",
            }}
          />
          <Area
            type="monotone"
            dataKey={y}
            stroke={getColor(0)}
            fill={getColor(0)}
            fillOpacity={0.25}
            strokeWidth={2}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function KpiView({ results }: { results: QueryResult }) {
  if (!results.rows || results.rows.length === 0) return null
  const firstRow = results.rows[0]
  const firstCol = results.columns[0]
  const numCol = pickNumericCol(results)
  const val = numCol ? firstRow[results.columns.indexOf(numCol)] : firstRow[0]
  return (
    <div className="flex flex-col items-center justify-center py-6">
      <p className="text-sm text-muted-foreground font-medium">{numCol || firstCol}</p>
      <p className="text-4xl font-bold tracking-tight mt-1 text-foreground">{formatNumber(val)}</p>
    </div>
  )
}

function TableView({ results }: { results: QueryResult }) {
  if (!results.columns || results.columns.length === 0) return null
  return (
    <div className="w-full h-full max-h-[360px] min-h-0 overflow-auto rounded-lg border bg-background/50 shadow-inner">
      <table className="w-full text-sm border-collapse">
        <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur-sm shadow-xs border-b">
          <tr className="border-b bg-muted/60">
            {results.columns.map((col, i) => (
              <th
                key={i}
                className="px-4 py-2.5 text-left font-semibold text-xs tracking-wide text-muted-foreground whitespace-nowrap"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/30">
          {(results.rows || []).map((row, i) => (
            <tr key={i} className="border-b border-border/20 last:border-0 hover:bg-muted/40 transition-colors">
              {row.map((cell, j) => (
                <td key={j} className="px-4 py-2 text-sm whitespace-nowrap">
                  {cell !== null && cell !== undefined ? String(cell) : "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {results.row_count > (results.rows?.length || 0) && (
        <div className="sticky bottom-0 z-10 border-t bg-muted/95 backdrop-blur-sm px-4 py-1.5 text-xs text-muted-foreground">
          Showing {results.rows.length} of {results.row_count} rows
        </div>
      )}
    </div>
  )
}

export function VisualizationRenderer({
  results,
  suggestions,
  compact = false,
}: {
  results: QueryResult
  suggestions?: VisualizationSuggestion[]
  compact?: boolean
}) {
  const [isMounted, setIsMounted] = useState(false)

  useEffect(() => {
    setIsMounted(true)
  }, [])

  if (!results || !results.columns || results.columns.length === 0) {
    return null
  }

  if (!suggestions || suggestions.length === 0) {
    return <TableView results={results} />
  }

  if (!isMounted) {
    return <TableView results={results} />
  }

  const renderContent = (v: VisualizationSuggestion) => {
    const config = v.config || {}
    switch (v.type) {
      case "bar_chart":
        return <BarChartView results={results} config={config} />
      case "pie_chart":
        return <PieChartView results={results} config={config} />
      case "line_chart":
        return <LineChartView results={results} config={config} />
      case "area_chart":
        return <AreaChartView results={results} config={config} />
      case "kpi":
        return <KpiView results={results} />
      case "table":
      default:
        return <TableView results={results} />
    }
  }

  if (compact) {
    return (
      <div className="w-full h-full min-h-0 flex flex-col">
        {suggestions.map((v, i) => (
          <div key={i} className="w-full h-full min-h-0 flex-1 flex flex-col">
            {renderContent(v)}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="w-full flex flex-col space-y-4">
      {suggestions.map((v, i) => (
        <div key={i} className="w-full rounded-xl border bg-card/60 p-4 shadow-xs flex flex-col">
          {v.title && <p className="mb-3 text-sm font-semibold text-foreground">{v.title}</p>}
          <div className="w-full min-h-[260px]">
            {renderContent(v)}
          </div>
        </div>
      ))}
    </div>
  )
}
