import type { SourceMetadata, SourceMetadataItem } from '../types/chat'

export function sourceMetadataItems(metadata?: SourceMetadata): SourceMetadataItem[] {
  if (!metadata) return []
  if (metadata.items) return metadata.items.filter(item => item.value)
  // Old messages keep their saved values without inventing missing dates or scopes.
  const items: SourceMetadataItem[] = []
  const add = (key: string, label: string, value?: string) => {
    if (value) items.push({ key, label, value, state: 'confirmed', evidence: metadata.basis
      ? [{ source: 'manual', text: metadata.basis }] : [] })
  }
  if (metadata.published_year) add('published_year', '发布年份', `${metadata.published_year}年`)
  const cohort = metadata.applicable_cohort ? `${metadata.applicable_cohort}级` : ''
  const audience = metadata.audience || ''
  add('audience', '适用对象', cohort && !audience.includes(cohort) ? [cohort, audience].filter(Boolean).join(' · ') : audience)
  add('coverage', '原记录范围', metadata.coverage)
  return items
}

export function sourceMetadataSummary(metadata?: SourceMetadata): string {
  const items = sourceMetadataItems(metadata)
  const date = items.find(item => item.state === 'confirmed' &&
    (item.key.endsWith('_year') || (item.key.endsWith('_date') && item.key !== 'printed_date')))
  if (date) return `${date.label} ${date.value}`
  const cohort = items.find(item => item.key === 'audience')?.value.match(/20\d{2}级(?:起)?/)
  return cohort ? `适用${cohort[0]}` : ''
}
