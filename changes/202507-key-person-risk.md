# Key Person Risk Analysis Feature Implementation

## Overview

This document details the implementation of a new Key Person Risk Analysis feature for the Kospex web interface. The feature provides insights into repository contributors and their impact on project continuity through detailed metrics and status analysis.

## Date: July 19, 2025

## Implementation Details

### 1. New Endpoint: `/key-person/{repo_id}`

**File Modified**: `/src/kweb2.py`

**Location**: Added after the existing `/repo/{repo_id}` endpoint (lines 663-693)

**Endpoint Specification**:
- **URL Pattern**: `/key-person/{repo_id}`
- **HTTP Method**: GET
- **Response Type**: HTMLResponse
- **Parameter**: `repo_id` (string) - Repository identifier in format `git_server~owner~repo`

**Backend Implementation**:
```python
@app.get("/key-person/{repo_id}", response_class=HTMLResponse)
async def key_person(request: Request, repo_id: str):
    """Display key person analysis for a repository"""
    try:
        logger.info(f"Key person view requested for repo: {repo_id}")

        kospex = KospexQuery()
        
        # Get commits summary data (same as repo view)
        commit_ranges = kospex.commit_ranges(repo_id)
        
        # Get developer status (same as repo view)
        developers = kospex.developers(repo_id=repo_id)
        developer_status = KospexUtils.repo_stats(developers, "last_commit")
        
        # Get key person data
        key_people = kospex.key_person(repo_id=repo_id)

        return templates.TemplateResponse(
            "key_person.html",
            {
                "request": request,
                "repo_id": repo_id,
                "ranges": commit_ranges,
                "developer_status": developer_status,
                "key_people": key_people
            }
        )
    except Exception as e:
        logger.error(f"Error in key_person endpoint: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
```

**Data Sources**:
- `KospexQuery.commit_ranges(repo_id)` - Commit summary statistics
- `KospexQuery.developers(repo_id)` - Developer data for status calculation
- `KospexUtils.repo_stats(developers, "last_commit")` - Developer status categorization
- `KospexQuery.key_person(repo_id)` - Key person analysis data

### 2. New Template: `key_person.html`

**File Created**: `/src/templates/key_person.html`

**Template Structure**: Based on `repo_view.html` but streamlined to focus on key person analysis

**Design System**:
- **Framework**: Tailwind CSS
- **Header**: Uses shared `_header.html` template for consistency
- **Layout**: Card-based design with proper spacing and shadows
- **Typography**: Hierarchical heading structure with proper contrast

### 3. Page Sections

#### 3.1 Repository Header
- **Title**: "Key Person Analysis - {repo_id}"
- **Purpose**: Clear identification of the repository being analyzed

#### 3.2 Commit Summary Table
- **Purpose**: High-level overview of repository commit health
- **Columns**: Active, Aging, Stale, Unmaintained
- **Data Source**: `commit_ranges` from backend
- **Features**: 
  - Non-sortable summary view
  - Center-aligned data
  - Link to detailed commit view

#### 3.3 Developer Status Area
- **Purpose**: Developer activity categorization
- **Columns**: Active, Aging, Stale, Unmaintained
- **Data Source**: `developer_status` from backend
- **Features**:
  - Conditional display (only shows if data available)
  - Center-aligned status counts
  - Consistent styling with commit summary

#### 3.4 Key People Table
- **Purpose**: Detailed analysis of key contributors
- **Columns**:
  1. **Author** (left-aligned) - Developer name/email
  2. **Commits** (right-aligned) - Total commit count
  3. **% Commits** (right-aligned) - Percentage of total repository commits
  4. **Last Commit** (right-aligned, monospace) - Date of most recent commit
  5. **First Commit** (right-aligned, monospace) - Date of first commit
  6. **Active Commits** (right-aligned) - Commits in last 90 days
  7. **% Active** (right-aligned) - Percentage of active commits

**Data Source**: `key_people` array from `KospexQuery.key_person()`

**Special Formatting**:
- **Fixed-width font**: Last Commit and First Commit columns use `font-mono` class
- **Right alignment**: All numeric and date columns
- **Sortable**: Default sort by commits (descending)

### 4. DataTable Configuration

**Commits Summary Table**:
```javascript
$("#commits_table").DataTable({
    responsive: true,
    paging: false,
    searching: false,
    info: false,
    ordering: false
});
```

**Developer Status Table**:
```javascript
$("#developer_status_table").DataTable({
    responsive: true,
    paging: false,
    searching: false,
    info: false,
    ordering: false
});
```

**Key People Table**:
```javascript
$("#key_people_table").DataTable({
    order: [[1, "desc"]], // Sort by commits descending
    responsive: true,
    pageLength: 25,
    lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, "All"]],
    dom: '<"flex flex-col sm:flex-row sm:items-center sm:justify-between mb-4"lf>rt<"flex flex-col sm:flex-row sm:items-center sm:justify-between mt-4"ip>',
    scrollX: true
});
```

### 5. Backend Data Integration

**KospexQuery.key_person() Method**:
- **Purpose**: Analyzes repository contributors and their impact
- **Parameters**: `repo_id` (required)
- **Returns**: Array of dictionaries with contributor metrics
- **Processing**:
  - Calculates commit percentages
  - Determines active vs. total contributions
  - Formats dates for display
  - Provides comprehensive contributor analysis

**Data Structure** (each key person record):
```python
{
    'author': 'developer@email.com',
    'commits': 150,
    '% commits': '25.5%',
    'last_commit': '2025-07-15',
    'first_commit': '2024-01-10', 
    'active_commits': 45,
    '% active': '30.2%'
}
```

### 6. User Experience Features

**Responsive Design**:
- Mobile-friendly table layouts
- Horizontal scrolling for wide tables
- Responsive navigation and spacing

**Accessibility**:
- Semantic HTML structure
- Proper heading hierarchy
- Screen reader friendly table headers
- High contrast design

**Performance**:
- Efficient data loading
- Client-side table sorting and filtering
- Pagination for large datasets

### 7. Usage Examples

**URL Access**:
```
/key-person/github.com~kospex~kospex
/key-person/bitbucket.org~myorg~myrepo
/key-person/gitlab.com~company~project
```

**Navigation Integration**:
The page can be linked from repository views or developer analysis pages to provide focused key person risk assessment.

## Benefits

1. **Risk Assessment**: Identify key contributors whose departure would impact project continuity
2. **Resource Planning**: Understand contributor distribution and engagement levels
3. **Knowledge Management**: Visualize expertise concentration and potential knowledge silos
4. **Team Health**: Monitor developer activity patterns and engagement trends
5. **Decision Support**: Data-driven insights for project management and resource allocation

## Technical Notes

- **Error Handling**: Comprehensive exception handling with logging
- **Template Consistency**: Follows established design patterns from `repo_view.html`
- **Data Formatting**: Consistent with other analytical views in the application
- **Performance**: Leverages existing query optimizations from KospexQuery methods

## Future Enhancements

Potential improvements for future iterations:
- Filtering by time ranges
- Export functionality for key person data
- Integration with risk scoring algorithms
- Visualization charts for contribution patterns
- Historical trend analysis