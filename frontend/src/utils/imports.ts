export const columnFields = [
  {
    key: 'date_column',
    label: '日期 *',
    aliases: ['日期', '交易时间', '交易日期', 'date', 'occurred_at'],
  },
  {
    key: 'amount_column',
    label: '金额 *',
    aliases: ['金额', '交易金额', 'amount'],
  },
  {
    key: 'type_column',
    label: '收支类型',
    aliases: ['收支', '收支类型', '类型', 'type'],
  },
  {
    key: 'category_column',
    label: '来源分类',
    aliases: ['分类', '类别', 'category'],
  },
  {
    key: 'merchant_column',
    label: '商户 / 对方',
    aliases: ['商户', '对方', '交易对方', 'merchant'],
  },
  {
    key: 'description_column',
    label: '备注',
    aliases: ['备注', '说明', 'description'],
  },
  {
    key: 'external_id_column',
    label: '来源单号',
    aliases: ['来源单号', '订单号', '交易单号', 'external_id'],
  },
  { key: 'currency_column', label: '币种', aliases: ['币种', 'currency'] },
] as const
export type ColumnKey = (typeof columnFields)[number]['key']

export function suggestColumns(headers: string[]) {
  const used = new Set<number>()
  return Object.fromEntries(
    columnFields.map((field) => {
      const index = headers.findIndex(
        (header, index) =>
          !used.has(index) &&
          (field.aliases as readonly string[]).includes(
            header.normalize('NFKC').trim().toLowerCase(),
          ),
      )
      if (index >= 0) used.add(index)
      return [
        field.key,
        index >= 0
          ? index
          : field.key === 'date_column' || field.key === 'amount_column'
            ? -1
            : null,
      ]
    }),
  ) as Record<
    Exclude<ColumnKey, 'date_column' | 'amount_column'>,
    number | null
  > & { date_column: number; amount_column: number }
}

export function sourceLabels(
  rows: { raw: string[] | null }[],
  column: number | null,
) {
  if (column === null) return []
  return [
    ...new Set(
      rows
        .map((row) => row.raw?.[column]?.normalize('NFKC').trim() || '')
        .filter(Boolean),
    ),
  ]
}

export function sampleCSV(today: string) {
  return (
    '\uFEFF日期,收支,金额,分类,商户,备注,来源单号,币种\r\n' +
    [
      `${today},收入,2000.00,工资,虚构公司,演示收入,DEMO-001,CNY`,
      `${today},支出,10.25,餐饮,虚构餐厅,演示支出,DEMO-002,CNY`,
      `${today},支出,abc,餐饮,虚构餐厅,错误金额示例,DEMO-003,CNY`,
      `${today},支出,10.25,餐饮,虚构餐厅,演示支出,DEMO-002,CNY`,
    ].join('\r\n') +
    '\r\n'
  )
}
