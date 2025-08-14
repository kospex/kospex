# GitHub Library Options and License Analysis

## Current Situation

Kospex currently uses **PyGitHub 2.6.1** which is licensed under **LGPL v3**. This creates potential licensing complications for commercial distribution and enterprise deployments.

## Alternative Python GitHub Libraries

### 1. **httpx/requests (Direct API calls)**
- **License**: MIT/Apache 2.0 ✅
- **Enterprise Support**: ✅ Full support
- **Maintenance**: Core Python ecosystem libraries
- **Pros**: 
  - Maximum control over API calls and rate limiting
  - No licensing restrictions
  - Works with any GitHub instance (Enterprise Server, GitHub.com)
  - Lightweight - only HTTP dependencies
  - Future-proof - always compatible with GitHub API changes
- **Cons**: 
  - More code required
  - Manual pagination handling
  - No built-in abstractions

**Example Implementation:**
```python
import httpx

class GitHubClient:
    def __init__(self, base_url="https://api.github.com", token=None):
        self.base_url = base_url.rstrip('/')
        self.headers = {'Authorization': f'token {token}'} if token else {}
    
    def get_repo_files(self, owner, repo):
        url = f"{self.base_url}/repos/{owner}/{repo}/git/trees/main?recursive=1"
        response = httpx.get(url, headers=self.headers)
        return response.json()

# Usage for Enterprise
client = GitHubClient("https://github.enterprise.com/api/v3", token)
```

### 2. **github3.py**
- **License**: BSD-3-Clause ✅
- **Enterprise Support**: ✅ Built-in enterprise support
- **GitHub**: https://github.com/sigmavirus24/github3.py
- **Status**: Actively maintained
- **Pros**:
  - Similar API to PyGitHub
  - Explicit enterprise server support
  - No licensing concerns
  - Good documentation
- **Cons**:
  - Additional dependency
  - Less widely adopted than PyGitHub

**Example:**
```python
import github3
# Enterprise Server
gh = github3.enterprise_login(token=token, url='https://github.enterprise.com')
repo = gh.repository(owner, repo_name)
```

### 3. **ghapi**
- **License**: Apache 2.0 ✅
- **Enterprise Support**: ❌ No enterprise support
- **GitHub**: https://github.com/fastai/ghapi
- **Status**: Well-maintained by FastAI team
- **Pros**:
  - Auto-generated from GitHub's OpenAPI spec
  - Always up-to-date with latest GitHub API
  - Clean, modern API design
- **Cons**:
  - **No Enterprise Server support** (hardcoded to GitHub.com)
  - Not suitable for enterprise environments

### 4. **gidgethub**
- **License**: Apache 2.0 ✅
- **Enterprise Support**: ✅ Configurable base URL
- **GitHub**: https://github.com/brettcannon/gidgethub
- **Status**: Maintained by Python core developer (Brett Cannon)
- **Pros**:
  - Async support
  - Webhook handling capabilities
  - High-quality codebase
- **Cons**:
  - Async-focused (may not fit current codebase)
  - Different API paradigm

## PyGitHub LGPL v3 License Implications

### **What You CAN Do:**
- ✅ Use PyGitHub in commercial software (including proprietary)
- ✅ Distribute your software without making it open source
- ✅ Keep your application code proprietary
- ✅ Charge for your software

### **What You MUST Do:**
1. **Provide LGPL notice** - Include PyGitHub's license text in your distribution
2. **Allow library replacement** - Users must be able to replace PyGitHub with a different version
3. **Distribute PyGitHub source** - Make PyGitHub's source code available to users
4. **Document the dependency** - Clearly state you're using LGPL software

### **What You CANNOT Do:**
- 🔴 Static linking without compliance - If bundling into single executable, may need to provide object files
- 🔴 Restrict user modifications - Users must be able to modify PyGitHub

### **Risk Assessment by Distribution Method:**

#### **Low Risk:**
- **Python pip distribution** - Users install PyGitHub separately ✅
- **Docker containers** - As long as users can replace the library ✅
- **Source distribution** - Users build from source ✅

#### **Medium Risk:**
- **Binary distributions** - Standalone executables (PyInstaller, etc.) require careful compliance
- **Commercial licensing** - Some organizations have blanket LGPL bans

#### **High Risk:**
- **Embedded systems** - Difficult to allow library replacement
- **Closed appliances** - Where users cannot modify software

## Why Organizations Avoid LGPL

1. **Corporate policies** - Many companies have blanket LGPL bans
2. **Compliance overhead** - Extra documentation and distribution requirements
3. **User restrictions** - Some enterprise customers cannot use LGPL software
4. **Distribution complexity** - Binary packaging becomes more complex
5. **Legal uncertainty** - LGPL interpretation can vary by jurisdiction

## Current Kospex Usage Analysis

### **Where PyGitHub is Used:**
- `src/gitfiles.py` - Repository file enumeration
- Dependency analysis and repository cloning
- GitHub API rate limit management
- Repository metadata extraction

### **Migration Effort Estimate:**
- **Low effort** - Most usage is basic API calls (GET requests)
- **Existing patterns** - Current code already handles pagination and rate limits
- **Test coverage** - Existing functionality can validate migration

## Recommendations

### **Primary Recommendation: httpx + Direct API**
**Benefits:**
- ✅ No licensing restrictions (MIT)
- ✅ Full enterprise server support
- ✅ Maximum control over rate limiting and caching
- ✅ Minimal dependencies
- ✅ Future-proof against GitHub API changes
- ✅ Easier to audit and debug

**Migration Path:**
1. Create `GitHubClient` wrapper class using httpx
2. Replace PyGitHub calls one module at a time
3. Maintain same external API for backward compatibility
4. Add comprehensive error handling and rate limiting

### **Alternative: github3.py**
If preferring a higher-level library:
- ✅ BSD-3-Clause license (no restrictions)
- ✅ Similar API to PyGitHub (easier migration)
- ✅ Built-in enterprise support
- ⚠️ Additional dependency to maintain

### **Not Recommended:**
- **ghapi** - No enterprise support
- **gidgethub** - Async paradigm doesn't fit current codebase

## Migration Strategy

### **Phase 1: Create Abstraction Layer**
Create a `GitHubInterface` class that wraps the current PyGitHub usage:
```python
class GitHubInterface:
    def __init__(self, base_url=None, token=None):
        # Can switch between PyGitHub and httpx
        pass
    
    def get_repo_files(self, repo_name):
        # Abstract method
        pass
```

### **Phase 2: Implement httpx Backend**
Replace PyGitHub implementation with httpx while maintaining the same interface.

### **Phase 3: Remove PyGitHub Dependency**
Update `requirements.txt` and `pyproject.toml` to remove PyGitHub.

### **Phase 4: Validation**
- Test with GitHub.com
- Test with GitHub Enterprise Server
- Validate rate limiting behavior
- Performance comparison

## Compliance Requirements if Keeping PyGitHub

If choosing to keep PyGitHub, ensure compliance by:

1. **Add LGPL notice** to `LICENSE` file:
   ```
   This software includes PyGitHub, licensed under LGPL v3.
   PyGitHub source code: https://github.com/PyGithub/PyGithub
   ```

2. **Document in README.md**:
   ```markdown
   ## Third-Party Licenses
   - PyGitHub: LGPL v3 (https://github.com/PyGithub/PyGithub/blob/main/COPYING.LESSER)
   ```

3. **For binary distributions**: Provide mechanism for users to replace PyGitHub

4. **Enterprise considerations**: Some enterprise customers may reject LGPL software

## Conclusion

**Recommendation**: Migrate to **httpx + direct API calls** to:
- Eliminate licensing restrictions
- Improve enterprise compatibility  
- Reduce dependencies
- Gain more control over GitHub API interactions

The migration effort is relatively small compared to the long-term benefits of licensing simplicity and enterprise compatibility.

---

*Last updated: January 2025*