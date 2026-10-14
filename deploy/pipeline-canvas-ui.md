# Pipeline canvas layout and connection visibility

## Read-only details and independent copies

Pipeline details now open a read-only canvas with node selection, parameter and
port inspection, shared-input links, connection focus, zoom and panning. The
viewer never saves a draft. Built-in pipelines cannot be edited, moved, rewired,
archived or overwritten. The backend also rejects updates to built-in IDs.

“Create from this Pipeline” opens an independent editable draft with a new
recovery key and no association to the original ID or version. Publishing creates
a new user Pipeline at v1. Method parameter defaults, fixed values, ordered links,
shared external inputs and conditional stages are retained. Conditional stages
keep their Pipeline control parameters; the copy editor exposes those controls.
Existing task confirmation and execution behavior remains unchanged.

The read-only `/api/library/pipelines/{id}/source?version=...` endpoint projects
the immutable execution graph into authoring source without writing to storage.
All 24 built-in sources are covered by compilation and preservation regressions.

## Behavior

- The editor uses the whole browser viewport. Method palette and parameter inspector
  can be hidden independently. Crossing below 1100 px closes the docks; users can
  reopen one dock at a time. Name/description stays collapsed by default.
- Validation has a separate, collapsible row with its own scroll area, capped at
  25% of viewport height. It cannot overlay the canvas.
- Canvas supports 2–200% zoom, a slider, zoom buttons, Ctrl/Command-wheel or trackpad
  pinch, fit-all, focus-selected-node, and reset. Focus the canvas for + / - / 0 / F.
- Background drag pans; node-header drag moves a step. Zoom leaves saved graph
  coordinates unchanged. Automatic arrangement reserves wider routing corridors.
- Port connections hit-test in the viewport's own Document or ShadowRoot, so
  dragging and clicking ports work inside the AGH workbench. Ports outside the
  current editor and disabled ports are rejected. Graph type and cycle checks
  still apply before saving a connection.
- Connections have separate colors and orthogonal candidate routes that favor
  avoiding node bodies. Shared inputs use dashed purple links. Hover/click highlights
  both endpoint ports and the chosen edge. The connection list states numbered steps
  and exact input/output labels, including repeated methods. Arbitrary dense graphs
  may still cross; focused edges render last while other edges fade.

## Module responsibilities

- `canvasGeometry.ts`: pure coordinate, fit, zoom anchoring and bounded routing.
- `useCanvasViewport.ts`: viewport events, scroll/zoom coordination and cleanup.
- `canvasConnections.ts`: graph bindings to renderable, labeled connections.
- `PipelineConnections.tsx`: edge rendering, focus and connection list.
- `PipelineCanvasNode.tsx`: node headers and conditional input/output ports.
- `PipelineCanvas.tsx`: gestures and coordination with the editor callbacks.
- `PipelineValidationPanel.tsx`: independent read-only validation presentation.

## Verification and maintenance

Geometry and connection tests cover fit, zoom anchoring, labeled edges and
shared inputs. Source compilation regressions cover the 24 built-in pipelines
and preserve parameter defaults, fixed values, ordered inputs and conditions.
Run the frontend tests, lint and build described in the [frontend guide](../frontend/README.md).

Deployment paths and runtime version checks are maintained in the
[server guide](server-deployment.md). Canvas source and geometry changes
require focused regressions; application tests alone do not validate physical
trackpad behavior or scientific outputs.
