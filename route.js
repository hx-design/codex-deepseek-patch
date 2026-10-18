// CodexDesktopDeepSeekFlashPatch 26.1013.40934 / 2026-10-18
// Keep provider changes limited to newly created local threads.
function codexPatchAppServerParams(method, params, hostId) {
  if (hostId !== 'local' || !params || typeof params !== 'object') return params;
  if (method === 'thread/list' && params.modelProviders == null)
    return {...params, modelProviders: []};
  if (method !== 'thread/start') return params;
  const providers = {
    'deepseek-flash': 'deepseek-desktop',
    'grok-4.5': 'easycli-desktop',
    'grok-4.7': 'easycli-desktop',
    'gemini-3.8-flash-high': 'easycli-desktop',
    'claude-opus-4-6-thinking': 'easycli-desktop',
  };
  const provider = Object.prototype.hasOwnProperty.call(providers, params.model) ? providers[params.model] : null;
  if (!provider) return params;
  if (params.modelProvider != null && params.modelProvider !== 'openai') return params;
  return {...params, modelProvider: provider, serviceTier: null,
    config: {...params.config, web_search: 'disabled'}};
}
