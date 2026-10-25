import { apiRequest } from './client'

export type WorkspaceType = 'personal' | 'business'
export type MemberRole = 'owner' | 'admin' | 'member' | 'viewer'
export interface Workspace {
  id: string
  name: string
  type: WorkspaceType
  base_currency: string
  role: MemberRole
  created_at: string
}
export interface Member {
  user_id: string
  email: string
  display_name: string
  role: MemberRole
}

export const listWorkspaces = () => apiRequest<Workspace[]>('/workspaces')
export const getWorkspace = (id: string) =>
  apiRequest<Workspace>(`/workspaces/${id}`)
export const createWorkspace = (
  name: string,
  type: WorkspaceType,
  baseCurrency: string,
  csrfToken: string,
) =>
  apiRequest<Workspace>(
    '/workspaces',
    {
      method: 'POST',
      body: JSON.stringify({ name, type, base_currency: baseCurrency }),
    },
    csrfToken,
  )
export const listMembers = (id: string) =>
  apiRequest<Member[]>(`/workspaces/${id}/members`)
export const addMember = (
  id: string,
  email: string,
  role: Exclude<MemberRole, 'owner'>,
  csrfToken: string,
) =>
  apiRequest<Member>(
    `/workspaces/${id}/members`,
    { method: 'POST', body: JSON.stringify({ email, role }) },
    csrfToken,
  )
