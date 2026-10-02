const API_PREFIX = "/api";

function apiPath(pathname) {
  if (pathname === API_PREFIX) return "/";
  if (pathname.startsWith(`${API_PREFIX}/`)) {
    return pathname.slice(API_PREFIX.length) || "/";
  }
  return pathname;
}

export default {
  async fetch(request, env) {
    const incomingUrl = new URL(request.url);
    const isApi = incomingUrl.pathname === API_PREFIX ||
      incomingUrl.pathname.startsWith(`${API_PREFIX}/`);
    const isAudio = incomingUrl.pathname.startsWith("/audio/");

    if (!isApi && !isAudio) {
      return env.ASSETS.fetch(request);
    }

    const origin = new URL(env.API_ORIGIN);
    const upstreamUrl = new URL(apiPath(incomingUrl.pathname) + incomingUrl.search, origin);
    const headers = new Headers(request.headers);
    // This is a same-origin browser request. Removing Origin avoids forwarding
    // browser CORS metadata to the API; session cookies and upload headers stay.
    headers.delete("origin");
    headers.delete("referer");
    headers.delete("host");

    try {
      const init = {
        method: request.method,
        headers,
        redirect: "manual",
      };
      if (request.method !== "GET" && request.method !== "HEAD") {
        init.body = request.body;
      }
      return await fetch(new Request(upstreamUrl, init));
    } catch (error) {
      return Response.json(
        { detail: "The ECHO API is temporarily unavailable." },
        { status: 502 },
      );
    }
  },
};
