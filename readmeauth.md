async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers = getHeaders();
  const token = await getTokenSingleton(async () => {
    return await getAccessToken();
  });

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}
