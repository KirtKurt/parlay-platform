const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

export function getApiBaseUrl() {
  if (!API_BASE_URL || API_BASE_URL === 'https://api.yourdomain.com') {
    return null;
  }

  return API_BASE_URL.replace(/\/$/, '');
}

export async function fetchFromApi<T>(path: string): Promise<T> {
  const baseUrl = getApiBaseUrl();

  if (!baseUrl) {
    throw new Error('NEXT_PUBLIC_API_BASE_URL is not configured. Add the deployed API Gateway URL in Amplify environment variables.');
  }

  const response = await fetch(`${baseUrl}${path}`, {
    headers: {
      'content-type': 'application/json'
    },
    cache: 'no-store'
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status} ${response.statusText}`);
  }

  return response.json() as Promise<T>;
}

export async function fetchHealth() {
  return fetchFromApi<{ ok: boolean; service: string; time: string }>('/v1/health');
}

export async function fetchTodaySlate() {
  return fetchFromApi('/v1/slates/today');
}

export async function fetchGameLineMovement(gameId: string) {
  return fetchFromApi(`/v1/games/${gameId}/line-movement?interval=15m`);
}
