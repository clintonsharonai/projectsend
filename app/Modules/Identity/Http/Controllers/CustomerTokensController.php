<?php

declare(strict_types=1);

namespace App\Modules\Identity\Http\Controllers;

use App\Http\Controllers\Controller;
use App\Models\User;
use App\Modules\Api\Auth\ApiTokens;
use App\Modules\Audit\Action;
use App\Modules\Audit\ActivityLogger;
use App\Modules\Files\Access\StaffLibraryScope;
use App\Modules\Files\Models\Folder;
use App\Modules\Identity\Permissions\Permission;
use Illuminate\Http\RedirectResponse;
use Illuminate\Http\Request;
use Illuminate\Validation\Rule;
use Inertia\Inertia;
use Inertia\Response;
use Laravel\Sanctum\PersonalAccessToken;

/**
 * Customer tokens — folder-bound, upload-only credentials an issuer mints
 * for customers.
 *
 * An issuer is a staff account holding create_api_tokens (e.g. a diagnostics
 * pipeline that hands each customer a throwaway upload token). This screen
 * lets that issuer, from the browser, mint a token that does exactly one
 * thing: push files into one chosen folder.
 *
 * Security, all inherited from the installation:
 *
 *  - **Gated** — the routes carry `can:create_api_tokens` (routes/settings.php).
 *  - **Scoped** — every read and mutation is bound to the caller's own tokens
 *    through the `tokens()` relation; a miss is a 404, so ids cannot be probed.
 *  - **Upload-only** — the token carries only `upload_only`, which the upload
 *    routes accept and no read route does, so a customer cannot list, download
 *    or comment with it. It can never carry create_api_tokens either.
 *  - **Folder-bound** — the token stores a folder_id; the API upload path
 *    (FilesController::store) forces every upload into that folder, so the
 *    customer cannot write anywhere else.
 *  - **Re-proven** — minting and revoking sit behind password.confirm.
 *  - **Audited** — both ends of a token's life land in the activity log.
 */
class CustomerTokensController extends Controller
{
    public function __construct(
        private readonly ActivityLogger $activity,
        private readonly StaffLibraryScope $scope,
    ) {}

    /**
     * The status of every customer token this issuer holds, most recent first.
     */
    public function index(Request $request): Response
    {
        $user = $request->user();
        assert($user !== null);

        return Inertia::render('settings/customer-tokens/index', [
            'tokens' => $this->tokensFor($user),
            // Flashed by store() and never persisted: the one and only time
            // the plaintext exists outside the caller's clipboard.
            'created_token' => $request->session()->get('created_customer_token'),
        ]);
    }

    /**
     * The minting form: a name, a folder, and an expiry. No ability
     * checkboxes — the token is always upload-only to the chosen folder.
     */
    public function create(Request $request): Response
    {
        $user = $request->user();
        assert($user !== null);

        return Inertia::render('settings/customer-tokens/create', [
            'folders' => $this->scope->folders($user)
                ->orderBy('path')
                ->orderBy('name')
                ->get(['id', 'name', 'path'])
                ->map(fn (Folder $folder): array => [
                    'id' => (int) $folder->id,
                    'name' => $folder->name,
                    'path' => $folder->path,
                ])
                ->values()
                ->all(),
            'defaults' => [
                'expires_in_days' => (int) config('api.tokens.default_days'),
                'max_days' => (int) config('api.tokens.max_days'),
            ],
        ]);
    }

    public function store(Request $request): RedirectResponse
    {
        $user = $request->user();
        assert($user !== null);

        $maxDays = (int) config('api.tokens.max_days');

        $validated = $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'folder_id' => ['required', 'integer', Rule::exists('folders', 'id')],
            'never_expires' => ['boolean'],
            'expires_in_days' => [
                Rule::requiredIf(fn (): bool => ! $request->boolean('never_expires')),
                'nullable', 'integer', 'min:1', 'max:'.$maxDays,
            ],
        ]);

        // The folder must be one this issuer may actually put content into —
        // the same rule the API upload enforces — so a token can never be
        // bound to a folder its owner cannot write to.
        $folder = $this->scope->folders($user)->whereKey($validated['folder_id'])->first();
        abort_unless($folder instanceof Folder && Folder::uploadableBy($user, $folder), 422);

        $expiresAt = $request->boolean('never_expires')
            ? null
            : now()->addDays((int) $validated['expires_in_days']);

        $token = $user->createToken($validated['name'], [Permission::UploadOnly->value], $expiresAt);
        $token->accessToken->folder_id = $folder->id;
        $token->accessToken->save();

        $this->activity->log(Action::ApiTokenCreated, $user, context: [
            'token_name' => $validated['name'],
            'folder_id' => $folder->id,
            'folder_name' => $folder->name,
            'abilities' => [Permission::UploadOnly->value],
            'expires_at' => $expiresAt?->toIso8601String(),
        ]);

        return redirect()->route('customer-tokens.index')->with('created_customer_token', [
            'name' => $validated['name'],
            'plain_text' => $token->plainTextToken,
        ]);
    }

    public function destroy(Request $request, string $token): RedirectResponse
    {
        $user = $request->user();
        assert($user !== null);

        $accessToken = $this->findOwnToken($user, $token);

        $this->activity->log(Action::ApiTokenRevoked, $user, context: [
            'token_name' => $accessToken->name,
        ]);

        $accessToken->delete();

        return back();
    }

    /**
     * Scoped to the caller's own tokens: the relation is what enforces it.
     * A miss is a 404 rather than a 403, so ids cannot be probed.
     */
    private function findOwnToken(User $user, string $token): PersonalAccessToken
    {
        $accessToken = $user->tokens()->whereKey($token)->first();

        abort_unless($accessToken instanceof PersonalAccessToken, 404);

        return $accessToken;
    }

    /**
     * @return list<array<string, mixed>>
     */
    private function tokensFor(User $user): array
    {
        $tokens = $user->tokens()->orderByDesc('created_at')->get();

        $folderNames = Folder::query()
            ->whereIn('id', $tokens->pluck('folder_id')->filter()->unique())
            ->pluck('name', 'id');

        return array_values($tokens
            ->map(fn (PersonalAccessToken $token): array => [
                'id' => (string) $token->getKey(),
                'name' => $token->name,
                'folder_id' => $token->folder_id !== null ? (int) $token->folder_id : null,
                'folder_name' => $token->folder_id !== null ? ($folderNames[$token->folder_id] ?? null) : null,
                'last_used_at' => $token->last_used_at?->toIso8601String(),
                'expires_at' => $token->expires_at?->toIso8601String(),
                'expired' => ! ApiTokens::isActive($token),
                'created_at' => $token->created_at?->toIso8601String(),
            ])
            ->all());
    }
}
