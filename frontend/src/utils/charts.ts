import type { EChartsCoreOption } from 'echarts/core'
import type { Dashboard } from '../api/analytics'
import { positiveCategories } from './analytics'
import { money } from './format'

type DailyKey =
  | 'income'
  | 'expense'
  | 'net'
  | 'cash_in'
  | 'cash_out'
  | 'net_cash_flow'
  | 'closing_balance'
interface TooltipItem {
  seriesName: string
  seriesIndex: number
  dataIndex: number
}

const colors = [
  '#91ead0',
  '#ffbd8b',
  '#a7baff',
  '#f3dc91',
  '#cfacff',
  '#8cd4ec',
]
const base = {
  animation: false,
  color: colors,
  textStyle: { color: '#c8d8df', fontFamily: 'Microsoft YaHei, sans-serif' },
}

export function dailyChart(
  report: Dashboard,
  series: { name: string; key: DailyKey; type: 'bar' | 'line' }[],
): EChartsCoreOption {
  return {
    ...base,
    legend: { top: 0, textStyle: { color: '#c8d8df' }, itemWidth: 14 },
    grid: { top: 48, left: 12, right: 20, bottom: 60, containLabel: true },
    tooltip: {
      trigger: 'axis',
      // 纯文本提示避免分类名或其他用户输入成为 HTML。金额回查原始字符串。
      renderMode: 'richText',
      confine: true,
      formatter: (input: unknown) => {
        const items = (Array.isArray(input) ? input : [input]) as TooltipItem[]
        const row = report.daily[items[0]?.dataIndex ?? -1]
        if (!row) return ''
        return [
          row.date,
          ...items.map(
            (item) =>
              `${item.seriesName}：${money(row[series[item.seriesIndex]!.key])} ${report.currency}`,
          ),
        ].join('\n')
      },
    },
    xAxis: {
      type: 'category',
      data: report.daily.map((row) => row.date),
      axisLabel: {
        formatter: (value: string) => value.slice(5),
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#304a5a' } },
      axisLabel: { color: '#aabec9' },
    },
    dataZoom: [
      { type: 'inside' },
      {
        type: 'slider',
        height: 18,
        bottom: 6,
        borderColor: '#355162',
        textStyle: { color: '#aabec9' },
        showDetail: false,
      },
    ],
    series: series.map((item) => ({
      name: item.name,
      type: item.type,
      // Number 仅供 ECharts 绘制坐标；汇总、排序、卡片、提示均使用精确金额。
      data: report.daily.map((row) => Number(row[item.key])),
      showSymbol: false,
      barMaxWidth: 16,
      lineStyle: { width: 2 },
    })),
  }
}

export function categoryChart(report: Dashboard): EChartsCoreOption {
  const categories = positiveCategories(report.categories)
  return {
    ...base,
    tooltip: {
      trigger: 'item',
      renderMode: 'richText',
      confine: true,
      formatter: (input: unknown) => {
        const item = categories[(input as TooltipItem).dataIndex]
        return item
          ? `${item.name}\n${money(item.amount)} ${report.currency}`
          : ''
      },
    },
    series: [
      {
        type: 'pie',
        radius: ['42%', '72%'],
        center: ['50%', '50%'],
        label: { show: false },
        data: categories.map((item) => ({
          name: item.name,
          value: Number(item.amount),
        })),
        emphasis: { scale: true, label: { show: false } },
      },
    ],
  }
}
