// CodexDesktopDeepSeekFlashPatch: local provider routing and model-specific defaults.
function codexPatchModelRoutes() {
  return /* MODEL_ROUTES_BEGIN */{"deepseek-flash":{"provider":"deepseek-desktop"}}/* MODEL_ROUTES_END */;
}
function codexPatchAppServerParams(method, params, hostId) {
  if (hostId !== 'local' || !params || typeof params !== 'object') return params;
  if (method === 'thread/list' && params.modelProviders == null)
    return {...params, modelProviders: []};
  if (method !== 'thread/start') return params;
  const routes = codexPatchModelRoutes();
  const route = Object.prototype.hasOwnProperty.call(routes, params.model) ? routes[params.model] : null;
  if (!route) return params;
  const provider = route.provider;
  if (params.modelProvider != null && params.modelProvider !== 'openai' &&
      !(route.context_window && params.modelProvider === provider)) return params;
  const config = {...params.config, web_search: 'disabled'};
  if (route.context_window) {
    const requested = config.model_context_window;
    const window = Number.isSafeInteger(requested) && requested > 0
      ? Math.min(requested, route.max_context_window) : route.context_window;
    config.model_context_window = window;
    const compact = Math.min(route.compact_limit, Math.floor(window * 0.9));
    const requestedCompact = config.model_auto_compact_token_limit;
    config.model_auto_compact_token_limit = Number.isSafeInteger(requestedCompact) && requestedCompact > 0
      ? Math.min(requestedCompact, compact) : compact;
  }
  return {...params, modelProvider: provider, serviceTier: null, config};
}
