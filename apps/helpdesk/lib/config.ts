/**
 * The rs-api base URL. `NEXT_PUBLIC_RS_API_URL` is read when `next dev` or `next build` runs and inlined into the
 * page, so set it before either; it defaults to the API's own default.
 */
export const API_BASE: string = (process.env.NEXT_PUBLIC_RS_API_URL || "http://127.0.0.1:8787").replace(/\/+$/, "");
