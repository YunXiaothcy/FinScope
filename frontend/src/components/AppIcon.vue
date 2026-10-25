<script setup lang="ts">
withDefaults(
  defineProps<{
    name:
      | 'overview'
      | 'ledger'
      | 'plus'
      | 'analysis'
      | 'more'
      | 'arrow'
      | 'check'
      | 'clock'
      | 'import'
      | 'receipt'
      | 'business'
      | 'switch'
    size?: number
  }>(),
  { size: 20 },
)

const paths = {
  import: 'M12 3v12 m-4-4 4 4 4-4 M4 16v4h16v-4',
  receipt: 'M6 3h12v18l-3-2-3 2-3-2-3 2Z M9 7h6 M9 11h6 M9 15h3',
  business: 'M4 8h16v12H4Z M9 8V4h6v4 M4 12h16 M12 11v3',
  switch: 'M4 7h15 m-4-4 4 4-4 4 M20 17H5 m4-4-4 4 4 4',
  overview: 'M4 14h4v6H4z M10 9h4v11h-4z M16 4h4v16h-4z',
  ledger:
    'M6 3h12a2 2 0 0 1 2 2v16H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z M4 17h16 M8 7h8 M8 11h5',
  plus: 'M12 5v14 M5 12h14',
  analysis: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
  more: 'M5 11v2 M12 11v2 M19 11v2',
  arrow: 'M5 12h14 m-6-6 6 6-6 6',
  check: 'm5 12 4 4L19 6',
  clock: 'M12 8v4l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
}
</script>

<template>
  <svg
    class="app-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path :d="paths[name]" />
  </svg>
</template>
