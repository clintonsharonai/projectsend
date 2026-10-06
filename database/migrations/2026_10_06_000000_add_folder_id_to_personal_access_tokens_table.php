<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

/**
 * Binds a token to a single library folder. A token with a folder_id is a
 * customer upload token: the API forces every upload it makes into that
 * folder (see FilesController::store), and the token carries only the
 * upload_only ability, so it cannot read, list or download anything else.
 *
 * Nullable on purpose — ordinary tokens (the personal API-tokens screen,
 * the POST /api/v1/tokens endpoint) have no folder and behave exactly as
 * before. Only the customer-tokens screen sets this.
 */
return new class extends Migration
{
    public function up(): void
    {
        Schema::table('personal_access_tokens', function (Blueprint $table) {
            $table->unsignedBigInteger('folder_id')->nullable()->after('abilities')->index();
        });
    }

    public function down(): void
    {
        Schema::table('personal_access_tokens', function (Blueprint $table) {
            $table->dropIndex(['folder_id']);
            $table->dropColumn('folder_id');
        });
    }
};
