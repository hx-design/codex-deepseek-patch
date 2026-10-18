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
  if (params.modelProvider != null && params.modelProvider !== 'openai' &&
      !(provider === 'easycli-desktop' && params.modelProvider === provider)) return params;
  const config = {...params.config, web_search: 'disabled'};
  if (provider === 'easycli-desktop') {
    const contextWindows = {
      'grok-4.5': 500000,
      'grok-4.7': 500000,
      'gemini-3.8-flash-high': 1048576,
      'claude-opus-4-6-thinking': 1000000,
    };
    const maximum = contextWindows[params.model];
    const requested = config.model_context_window;
    const window = Number.isSafeInteger(requested) && requested > 0 ? Math.min(requested, maximum) : maximum;
    config.model_context_window = window;
    // Keep room for the next output/tool results without shrinking the model's
    // advertised window. A chat may explicitly choose a smaller threshold.
    const compact = Math.floor(window * 0.9);
    const requestedCompact = config.model_auto_compact_token_limit;
    config.model_auto_compact_token_limit = Number.isSafeInteger(requestedCompact) && requestedCompact > 0
      ? Math.min(requestedCompact, compact) : compact;
  }
  return {...params, modelProvider: provider, serviceTier: null,
    config};
}
