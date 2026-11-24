/** The router the tests' `next/navigation` mock returns: it records every navigation instead of performing it. */
export const router = {
  episodeId: "",
  pathname: "/",
  pushed: [] as string[],
  push(href: string) {
    this.pushed.push(href);
  },
  replace(href: string) {
    this.pushed.push(href);
  },
  back() {},
  forward() {},
  refresh() {},
  prefetch() {},
  reset() {
    this.episodeId = "";
    this.pathname = "/";
    this.pushed = [];
  },
};
