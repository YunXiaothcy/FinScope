import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'vite'
import * as echarts from 'echarts'

test('dashboard options render charts at phone and desktop widths with exact text tooltips', async () => {
  const server = await createServer({
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true, hmr: false, ws: false },
    appType: 'custom',
  })
  try {
    const { dailyChart, categoryChart } = await server.ssrLoadModule(
      '/src/utils/charts.ts',
    )
    const report = {
      currency: 'CNY',
      daily: [
        {
          date: '2026-10-01',
          income: '9999999999999999.99',
          expense: '20.00',
          net: '9999999999999979.99',
          net_cash_flow: '10.00',
          closing_balance: '100.00',
        },
      ],
      categories: [
        {
          category_id: '1',
          name: '<img src=x onerror=alert(1)>',
          kind: 'expense',
          amount: '20.00',
        },
        { category_id: '2', name: '退款', kind: 'expense', amount: '-5.00' },
      ],
    }
    const trend = dailyChart(report, [
      { name: '收入', key: 'income', type: 'bar' },
    ])
    assert.equal(trend.tooltip.renderMode, 'richText')
    assert.match(
      trend.tooltip.formatter([
        { dataIndex: 0, seriesIndex: 0, seriesName: '收入' },
      ]),
      /9,999,999,999,999,999\.99/,
    )
    const pie = categoryChart(report)
    assert.equal(pie.series[0].data.length, 1)
    assert.equal(pie.tooltip.renderMode, 'richText')
    assert.match(pie.tooltip.formatter({ dataIndex: 0 }), /20\.00 CNY/)
    for (const width of [288, 900]) {
      for (const option of [
        trend,
        pie,
        dailyChart(report, [
          { name: '余额', key: 'closing_balance', type: 'line' },
        ]),
      ]) {
        const chart = echarts.init(null, null, {
          renderer: 'svg',
          ssr: true,
          width,
          height: 280,
        })
        try {
          chart.setOption(option)
          const svg = chart.renderToSVGString()
          assert.match(svg, /<svg/)
          assert.match(svg, /<path/)
          assert.doesNotMatch(svg, /NaN|Infinity|<img/)
        } finally {
          chart.dispose()
        }
      }
    }
  } finally {
    await server.close()
  }
})
