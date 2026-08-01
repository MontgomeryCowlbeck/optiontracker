import { useEffect, useState } from "react";

export interface Route {
  view: string;
  param?: string;
}

function parse(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [view, ...rest] = h.split("/");
  return { view: view || "morning", param: rest.length ? rest.join("/") : undefined };
}

export function navigate(view: string, param?: string) {
  location.hash = "/" + view + (param ? "/" + param : "");
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
