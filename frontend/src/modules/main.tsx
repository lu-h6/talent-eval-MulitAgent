import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "../style.css";
import "../refinements.css";
import "./modules.css";

createRoot(document.getElementById("modules-root")!).render(<App />);
