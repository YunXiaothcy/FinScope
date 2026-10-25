export function workspaceNavigation(base: string, business: boolean) {
  const items = [
    {
      id: 'dashboard',
      label: '财务总览',
      shortLabel: '总览',
      icon: 'overview',
      group: 'main',
    },
    {
      id: 'finance',
      label: '账本',
      shortLabel: '账本',
      icon: 'ledger',
      group: 'main',
    },
    ...(business
      ? [
          {
            id: 'business',
            label: '商户经营',
            shortLabel: '经营',
            icon: 'business',
            group: 'main',
          } as const,
        ]
      : []),
    {
      id: 'analysis',
      label: '财务分析',
      shortLabel: '分析',
      icon: 'analysis',
      group: 'main',
    },
    {
      id: 'imports',
      label: '账单导入',
      shortLabel: '导入',
      icon: 'import',
      group: 'tools',
    },
    {
      id: 'receipts',
      label: '票据识别',
      shortLabel: '票据',
      icon: 'receipt',
      group: 'tools',
    },
  ] as const
  return items.map((item) => ({ ...item, to: `${base}/${item.id}` }))
}

export function isNavigationActive(path: string, target: string) {
  return path === target || path.startsWith(`${target}/`)
}
