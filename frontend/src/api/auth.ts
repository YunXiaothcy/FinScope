import { apiRequest } from './client'

export interface User {
  id: string
  email: string
  display_name: string
  created_at: string
}
export interface AuthResponse {
  user: User
  csrf_token: string
}

export const getSession = () => apiRequest<AuthResponse>('/auth/session')
export const registerUser = (
  email: string,
  password: string,
  displayName: string,
) =>
  apiRequest<AuthResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, display_name: displayName }),
  })
export const loginUser = (email: string, password: string) =>
  apiRequest<AuthResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
export const logoutUser = (csrfToken: string) =>
  apiRequest<void>('/auth/logout', { method: 'POST' }, csrfToken)
export const updateProfile = (displayName: string, csrfToken: string) =>
  apiRequest<User>(
    '/auth/profile',
    { method: 'PATCH', body: JSON.stringify({ display_name: displayName }) },
    csrfToken,
  )
export const changePassword = (
  currentPassword: string,
  newPassword: string,
  csrfToken: string,
) =>
  apiRequest<void>(
    '/auth/change-password',
    {
      method: 'POST',
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    },
    csrfToken,
  )
