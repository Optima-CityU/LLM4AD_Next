export const PROJECT_CONTEXT_DIRECTORY = "project_context"

/** Place optional author materials under a shared stage context. */
export function projectContextUploads(files: File[]) {
  return files.map((file) => ({
    file,
    relativePath: `${PROJECT_CONTEXT_DIRECTORY}/${file.name}`,
  }))
}

/** Detect uploaded author materials in the current source manifest. */
export function hasProjectContextFiles(manifest: string[]) {
  return manifest.some((path) =>
    path.startsWith(`${PROJECT_CONTEXT_DIRECTORY}/`),
  )
}
