import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.js";
import "./style.css";

class ErrorBoundary extends React.Component {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed
      ? React.createElement("div", { role: "alert", className: "state err" },
          "The viewer encountered an error. Reload the page to try again.")
      : this.props.children;
  }
}
createRoot(document.getElementById("root")).render(
  React.createElement(ErrorBoundary, null, React.createElement(App)),
);
