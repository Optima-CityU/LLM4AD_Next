package com.dadastory.omni_ai_router.dto;

import lombok.Data;

/**
 * Common response wrapper used for LiteLLM client results.
 *
 * @param <T> payload type
 */
@Data
public class Result<T> {
    private int code;
    private String message;
    private T data;

    /**
     * Creates a result instance.
     *
     * @param code response code
     * @param message response message
     * @param data response payload
     */
    public Result(int code, String message, T data) {
        this.code = code;
        this.message = message;
        this.data = data;
    }

    /**
     * Builds a successful result with the default success message.
     *
     * @param data response payload
     * @param <T> payload type
     * @return success result
     */
    public static <T> Result<T> success(T data) {
        return new Result<>(200, "success", data);
    }

    /**
     * Builds a successful result with a custom message.
     *
     * @param message response message
     * @param data response payload
     * @param <T> payload type
     * @return success result
     */
    public static <T> Result<T> success(String message, T data) {
        return new Result<>(200, message, data);
    }

    /**
     * Builds a failed result with an explicit code.
     *
     * @param code response code
     * @param message response message
     * @param <T> payload type
     * @return failure result
     */
    public static <T> Result<T> failure(int code, String message) {
        return new Result<>(code, message, null);
    }

    /**
     * Builds a failed result with the default internal error code.
     *
     * @param message response message
     * @param <T> payload type
     * @return failure result
     */
    public static <T> Result<T> failure(String message) {
        return new Result<>(500, message, null);
    }

    /**
     * Checks whether this result code is in the HTTP success range.
     *
     * @return {@code true} when the code is 2xx
     */
    public boolean isSuccess() {
        return code >= 200 && code < 300;
    }

}
