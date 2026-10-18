// CodexDesktopDeepSeekFlashPatch 26.1013.40934 / 2026-10-18
// Keep provider changes limited to newly created local threads.
function codexPatchAppServerParams(method, params, hostId) {
  if (hostId !== 'local' || !params || typeof params !== 'object') return params;
  if (method === 'thread/list' && params.modelProviders == null)
    return {...params, modelProviders: []};
  if (method !== 'thread/start' || params.model !== 'deepseek-flash') return params;
  if (params.modelProvider != null && params.modelProvider !== 'openai') return params;
  return {...params, modelProvider: 'deepseek-desktop', serviceTier: null,
    config: {...params.config, web_search: 'disabled'}};
}
