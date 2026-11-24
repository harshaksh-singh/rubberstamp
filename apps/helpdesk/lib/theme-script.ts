export const THEME_KEY = "rs-helpdesk-theme";

/** Runs in <head> before the first paint: the saved choice, else the system setting. */
export const THEME_SCRIPT = `(function(){try{var c=localStorage.getItem("${THEME_KEY}");var d=c==="dark"||((!c||c==="system")&&matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.classList.toggle("dark",d);}catch(e){}})();`;
