import { projectContextUploads } from "@/lib/projectContext"

export type PaperSourcePurpose = "source" | "project_context"

export type PaperSourceUpload =
  | {
      files: Array<File | { file: File; relativePath: string }>
      purpose?: "source"
    }
  | { files: File[]; purpose: "project_context" }

export function paperSourceRelativePath(file: File): string {
  return file.webkitRelativePath || file.name
}

export function paperSourceUploadTooLarge(files: File[]): boolean {
  return files.reduce((total, file) => total + file.size, 0) > 100 * 1024 * 1024
}

export function paperSourceUploadFormData(selection: PaperSourceUpload) {
  const uploads =
    selection.purpose === "project_context"
      ? projectContextUploads(selection.files)
      : selection.files.map((item) =>
          item instanceof File
            ? { file: item, relativePath: paperSourceRelativePath(item) }
            : item,
        )
  return {
    files: uploads.map(({ file }) => file),
    relative_paths: uploads.map(({ relativePath }) => relativePath),
  }
}
