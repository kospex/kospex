# Collaboration Network Graph Implementation

## Feature Description

Added a comprehensive network graph visualization for repository collaboration analysis that displays author-committer relationships as an interactive D3.js force-directed graph with advanced features including zoom/pan, node pinning, side panel details, and export functionality.

## Implementation Details

### 1. FastAPI Endpoints Added

**File:** `src/kweb2.py` (lines 435-465)

Added two new endpoints:

- **`/collab/graph/{repo_id}`** - HTML page serving the network visualization
- **`/api/collab/graph/{repo_id}`** - JSON API endpoint returning collaboration data

```python
@app.get("/collab/graph/{repo_id}", response_class=HTMLResponse)
async def collab_graph(request: Request, repo_id: str):
    """Display network graph visualization of repository collaboration"""

@app.get("/api/collab/graph/{repo_id}", response_class=JSONResponse)
async def collab_graph_data(request: Request, repo_id: str):
    """Return JSON data for collaboration network graph"""
```

### 2. HTML Template Created

**File:** `src/templates/collab_graph.html`

- Responsive design using TailwindCSS
- D3.js integration for network visualization
- Interactive elements with tooltips and drag functionality
- Legend explaining visualization components
- Loading states and error handling

### 3. Data Processing

**Data Format:** Processes collaboration data in format:
```json
[
  {
    "author_email": "102937168+ericinfins@users.noreply.github.com",
    "committer_email": "noreply@github.com", 
    "commits": 86
  },
  {
    "author_email": "105425571+john-masters@users.noreply.github.com",
    "committer_email": "noreply@github.com",
    "commits": 1
  }
]
```

**Backend Integration:** Reuses existing `KospexQuery.get_collabs()` method for data retrieval.

## Visualization Features

### Node Representation
- **Nodes:** Each unique email address (author or committer)
- **Size:** Scaled by total commit count (5-20px radius)
- **Color:** Blue (#3b82f6) for all developers
- **Labels:** Display username portion of email addresses

### Link Representation
- **Regular Links:** Lines between different email addresses
- **Thickness:** Scaled by collaboration frequency (1-8px width)
- **Self-loops:** Green circular arrows for author === committer cases
- **Markers:** Arrow markers on self-loops for directional indication
- **Commit Labels:** Numerical commit counts displayed on all links and self-loops

### Interactive Elements
- **Draggable Nodes:** Users can drag nodes to rearrange layout (nodes stay pinned where dragged)
- **Hover Tooltips:** Show developer email and commit counts
- **Clickable Nodes:** Open left-side panel with detailed developer information
- **Double-click Nodes:** Unpin nodes to return to force simulation
- **Force Simulation:** Disabled during dragging for precise positioning

### Layout Algorithm
- **Force-directed graph** using D3.js simulation
- **Forces applied:**
  - Link force (distance: 100px) - only when regular links exist
  - Charge force (strength: -200, repulsion)
  - Center force (graph centering)
  - Collision detection (prevents node overlap)
  - X/Y positioning forces (strength: 0.1, gentle centering)
- **Boundary constraints:** Nodes constrained to stay within visible area
- **Static positioning:** Simulation stops during node dragging for precise control

## UI/UX Features

### Navigation
- **Breadcrumb:** Shows current repository ID
- **Back Link:** "View Table" button links to tabular `/collab/{repo_id}` view
- **Consistent Header:** Matches existing Kospex web interface

### Advanced Controls
- **Zoom/Pan:** Mouse scroll to zoom (0.1x to 10x), click-drag to pan entire graph
- **Reset Zoom:** Button to return to default view with smooth animation
- **Download Graph:** Export current view as high-resolution PNG image
- **Node Pinning:** Drag nodes to pin in place, double-click to unpin

### Legend & Visual Indicators
- **Interactive Legend:** Visual explanation with actual styled elements
- **Node States:** Orange border indicates pinned nodes
- **Control Instructions:** Complete guide to all interaction patterns
- **Icon Indicators:** Clear visual symbols for each action type

### Side Panel Details
- **Left-side Panel:** Slides in from left when clicking nodes
- **Developer Information:** Email, username, commit statistics
- **Collaboration Details:** List of collaborators with commit counts
- **Quick Actions:** Direct link to full developer profile
- **Multiple Close Options:** Close button, overlay click, or Escape key

### Responsive Design
- **Container:** 80vh height with 600px minimum, full width utilization
- **Mobile-friendly:** Scales appropriately on different screen sizes
- **Accessibility:** Proper contrast ratios and semantic HTML
- **Performance:** Optimized for smooth interactions and animations

## Technical Implementation

### JavaScript Libraries
- **D3.js v7** - Force-directed graph visualization
- **Existing assets** - Reuses available `/static/js/d3.min.js`

### Scaling Functions
```javascript
// Node size scaling
const nodeScale = d3.scaleSqrt()
    .domain([1, maxCommits])
    .range([5, 20]);

// Link thickness scaling  
const linkScale = d3.scaleLinear()
    .domain([1, maxLinkCommits])
    .range([1, 8]);
```

### Self-Loop Implementation
- **Path generation:** Circular arc paths using SVG path commands
- **Dynamic positioning:** Radius based on node size + 15px offset
- **Visual distinction:** Green color (#22c55e) with arrow markers
- **Commit Count Labels:** Green bold text positioned above self-loops

### Advanced Features
- **Zoom/Pan System:** D3.js zoom behavior with scale constraints (0.1x to 10x)
- **Node Pinning:** Persistent positioning with visual feedback (orange borders)
- **Export Functionality:** SVG to Canvas conversion for high-quality PNG export
- **Side Panel:** Smooth CSS transitions with overlay backdrop
- **Commit Count Accuracy:** Fixed double-counting issue for proper node sizing

## Integration Points

### Existing Codebase Integration
- **Data source:** Uses existing `kospex_query.py:get_collabs()` method
- **Styling:** Consistent with existing TailwindCSS classes
- **Navigation:** Integrates with existing web interface header
- **Templates:** Follows established Jinja2 template patterns

### Performance Considerations
- **Client-side rendering:** D3.js handles visualization on client
- **Data optimization:** Processes collaboration data efficiently
- **Memory usage:** Scales well with typical repository collaboration data

## Testing Verification

The implementation provides:
1. **Visual feedback** for loading states
2. **Error handling** for API failures  
3. **Graceful degradation** if no collaboration data exists
4. **Cross-browser compatibility** through D3.js standard implementation

## Key Enhancements Made

### Data Accuracy Fixes
- **Fixed double-counting bug** in node size calculation (nodes showing 2x commit counts)
- **Proper commit aggregation** for self-commits vs collaboration commits
- **Accurate collaboration statistics** in side panel details

### User Experience Improvements
- **Expanded graph area** to use 80% of viewport height with full width
- **Left-side panel** for better information flow and readability
- **Static drag behavior** - other nodes don't move when dragging one node
- **Visual feedback** with orange borders for pinned nodes
- **Reliable icon display** using styled Unicode characters instead of emoji

### Advanced Interactions
- **Zoom/Pan functionality** with mouse scroll and drag
- **Node pinning system** with persistent positioning
- **Double-click to unpin** nodes back to simulation
- **High-quality PNG export** with 2x resolution scaling
- **Smooth animations** for all UI transitions

### Technical Robustness
- **Fixed dimension calculation** timing issues for proper width utilization
- **Boundary constraints** to prevent nodes from flying off screen
- **Performance optimization** by stopping simulation during drag operations
- **Cross-browser compatibility** with fallback handling

## Future Enhancement Opportunities

- **Filtering controls** for commit count thresholds
- **Time-based filtering** for collaboration periods
- **Advanced export options** (SVG, PDF formats)
- **Clustering algorithms** for large collaboration networks
- **Additional node metadata** (commit frequency, tenure, etc.)
- **Keyboard shortcuts** for common operations
- **Graph layouts** (circular, hierarchical, grid)
- **Animation controls** (speed, pause/resume simulation)